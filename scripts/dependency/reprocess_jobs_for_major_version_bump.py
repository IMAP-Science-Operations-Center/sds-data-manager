"""Determine jobs that need reprocessing due to a major version bump.

Run at deploy time with a checkout of the last deployed commit. The dependency YAML
from that commit is compared with the YAML in the installed imap-data-access.
"""

import argparse
import json
import logging
import subprocess
import sys
import tempfile
from pathlib import Path

import tomllib
from imap_data_access import VALID_DATALEVELS
from imap_data_access.dependencies import (
    DependencyConfigReader,
    ProcessingJobNode,
    get_kickoff_jobs,
)
from imap_data_access.dependencies.validation import (
    validate_dependency_yaml_versions,
)

logger = logging.getLogger(__name__)

# Where the dependency YAML lived in this repository before it moved to
# imap-data-access. Deployed commits from before the move still have it.
IN_REPO_YAML_DIR = Path("sds_data_manager/orchestration/dependencies")
# Where the dependency YAML lives inside an installed imap-data-access.
PACKAGED_YAML_DIR = Path("imap_data_access/dependencies/config_files")


def baseline_requirement(lock_file: Path) -> str:
    """Return a pip requirement for the imap-data-access pinned in a poetry.lock.

    Parameters
    ----------
    lock_file : Path
        The ``poetry.lock`` of the commit to compare against.

    Returns
    -------
    str
        ``imap-data-access==<version>`` for a PyPI release, or
        ``imap-data-access @ git+<url>@<commit>`` for a git pin.
    """
    lock = tomllib.loads(lock_file.read_text())
    for package in lock.get("package", []):
        if package["name"] != "imap-data-access":
            continue
        source = package.get("source")
        if source is None:
            return f"imap-data-access=={package['version']}"
        if source.get("type") == "git":
            return (
                f"imap-data-access @ git+{source['url']}@{source['resolved_reference']}"
            )
        raise ValueError(
            f"imap-data-access comes from an unsupported {source.get('type')!r} "
            f"source in {lock_file}"
        )
    raise ValueError(f"imap-data-access is not in {lock_file}")


def _pip_install(requirement: str, target: Path) -> None:
    """Install one package, without its dependencies, into a folder."""
    # pip's output goes to stderr: the deploy workflow parses this script's stdout.
    # The requirement comes from this repository's own poetry.lock.
    subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--quiet",
            "--no-deps",
            "--target",
            str(target),
            requirement,
        ],
        check=True,
        stdout=sys.stderr,
    )


def previous_yaml_dir(checkout: Path, work_dir: Path) -> Path:
    """Return the folder of dependency YAML files a checkout of this repository used.

    Parameters
    ----------
    checkout : Path
        A checkout of the commit to compare against, e.g. the last deployed one.
    work_dir : Path
        An empty folder to install that commit's imap-data-access into, if needed.

    Returns
    -------
    Path
        The folder holding that commit's ``imap_<instrument>_dependencies.yaml``
        files: this repository's own copy for commits from before the YAML moved
        to imap-data-access, and otherwise the copy packaged with the
        imap-data-access pinned in that commit's ``poetry.lock``.
    """
    in_repo = checkout / IN_REPO_YAML_DIR
    if in_repo.is_dir():
        return in_repo

    requirement = baseline_requirement(checkout / "poetry.lock")
    logger.info(f"Installing {requirement} to read its dependency YAML")
    _pip_install(requirement, work_dir)
    yaml_dir = work_dir / PACKAGED_YAML_DIR
    if not yaml_dir.is_dir():
        raise FileNotFoundError(
            f"{requirement} has no packaged dependency YAML at {yaml_dir}"
        )
    return yaml_dir


def get_updated_output_dependency_nodes(
    old_reader, new_reader
) -> list[ProcessingJobNode]:
    """Find the jobs that need reprocessing due to major_version bump.

    This function will loop through every potential job's output in the new
    dependency yaml and compare it against the output of the old potential
    job in the old dependency yaml. If the job is new, or one of its outputs
    is new, or an output's major_version increased, the potential job's
    dependency information node is added to the list of jobs that need to be
    reprocessed.

    old_reader: DependencyConfigReader
        Reader built from the previous dependency yaml files.
    new_reader: DependencyConfigReader
        Reader built from the current dependency yaml files.

    Returns
    -------
    list[ProcessingJobNode]
        Jobs whose output is new or had a major_version bump.
    """
    updated_nodes = []
    for job_key, new_potential_job in new_reader.config.items():
        old_potential_job = old_reader.config.get(job_key, None)
        if old_potential_job is None and new_potential_job not in updated_nodes:
            logger.info(
                f"New Job ({new_potential_job.source},{new_potential_job.data_type},"
                f"{new_potential_job.descriptor})"
            )
            # job didn't exist before, so it needs to be reprocessed
            updated_nodes.append(new_potential_job)
            continue
        for new_output in new_potential_job.outputs:
            # find the same output in the old node, if it's there
            old_output = next(
                (
                    output
                    for output in old_potential_job.outputs
                    if output.source == new_output.source
                    and output.data_type == new_output.data_type
                    and output.descriptor == new_output.descriptor
                ),
                None,
            )
            if old_output is None:
                logger.info(
                    f"New output ({new_output.source},{new_output.data_type},"
                    f"{new_output.descriptor}) has major_version="
                    f"{new_output.major_version}."
                )
                # output is new, so this job needs to be reprocessed
                if new_potential_job not in updated_nodes:
                    updated_nodes.append(new_potential_job)
                continue
            # Major version should never decrease. That is handled in the
            # validation code.
            elif new_output.major_version > old_output.major_version:
                logger.info(
                    f"Output product ({new_output.source},{new_output.data_type},"
                    f"{new_output.descriptor}) was bumped from major_version="
                    f"{old_output.major_version} to major_version="
                    f"{new_output.major_version}"
                )
                # major_version got bumped, so it needs to be reprocessed
                if new_potential_job not in updated_nodes:
                    updated_nodes.append(new_potential_job)

    return updated_nodes


def get_updated_root_node(
    node, nodes, new_reader, root_jobs
) -> list[ProcessingJobNode]:
    """Walk upstream from a changed node to find the furthest upstream job that changed.

    Reprocessing the furthest upstream job in a chain of changed jobs will trigger
    every other changed job downstream of it, so we don't need to reprocess those
    separately. This walks node's inputs back toward the start of the pipeline and
    keeps going upstream as long as the job that produced that input also changed.
    It stops and returns the current node once it hits a kickoff job (one of
    root_jobs, e.g. idex l1a, all, or hi l1b, hk) or once the upstream job
    didn't change.

    node: ProcessingJobNode
        The changed node we're finding the root for.
    nodes: list[ProcessingJobNode]
        All the jobs that have a major_version bump between the old and new
        dependency yaml.
    new_reader: DependencyConfigReader
        Reader built from the current dependency yaml files, used to look up which
        job produces a given input.
    root_jobs: list[ProcessingJobNode]
        The kickoff job for each pipeline, from get_kickoff_jobs(). If node is
        already one of these, there's nothing further upstream to check.

    Returns
    -------
    list[ProcessingJobNode]
        The furthest upstream changed job(s) in node's chain of changes.
    """
    root_nodes = []
    if node in root_jobs:
        return [node]

    for input_node in node.inputs:
        # not a real processing job upstream of this (e.g. spice, ancillary),
        # so there's nothing to walk further up
        # Only check inputs of the same source since major versions only get bumped
        # within the same source.
        # If the input node does not trigger the job, do not walk further up:
        #   - only triggering_deps (trigger_job=True) actually cause Dagster to
        #     re-trigger this node when they're reprocessed
        #   - walking past a non-triggering input would incorrectly fold this node
        #     into its upstream root, so it would never get explicitly reprocessed
        if (
            input_node.data_type not in VALID_DATALEVELS
            or input_node.source != node.source
            or not input_node.trigger_job
        ):
            continue
        upstream_node = new_reader.get_node_for_output(input_node)
        if upstream_node in nodes:
            # upstream also changed, so keep walking up that branch.
            root_nodes.extend(
                get_updated_root_node(upstream_node, nodes, new_reader, root_jobs)
            )

    # if no upstream root nodes were added this means the current node is the root node
    if not root_nodes:
        # upstream didn't change, so this node is as far back as we can go
        # e.g. IDEX l2b takes l1b msg and l2a sci as inputs. If only l2a gets a major
        # version bump, we don't want to also add l2b as its own root just
        # because l1b msg hasn't changed - reprocessing l2a will trigger l2b
        # anyway.
        root_nodes.append(node)

    return root_nodes


def reprocess_jobs_for_major_version_bump(
    old_reader: DependencyConfigReader, new_reader: DependencyConfigReader
) -> list[tuple[str, str, str]]:
    """Determine which jobs need to be reprocessed due to a major_version bump.

    This finds every job whose output major_version increased between old_reader
    and new_reader, and then walks each one back to the furthest upstream job in
    its chain of changes so we only trigger reprocessing once per pipeline
    instead of once per changed job. Takes readers instead of yaml paths so tests
    can build old_reader/new_reader however they want, instead of needing real
    yaml files on disk.

    old_reader: DependencyConfigReader
        Reader built from the previous dependency yaml files (the ones from the
        last deployment).
    new_reader: DependencyConfigReader
        Reader built from the current dependency yaml files.

    Returns
    -------
    list[tuple[str, str, str]]
        (source, data_type, descriptor) tuples for each job to reprocess.
    """
    nodes = get_updated_output_dependency_nodes(old_reader, new_reader)
    logger.info(
        f"Found {len(nodes)} updated output nodes: "
        f"{[(node.source, node.data_type, node.descriptor) for node in nodes]}"
    )

    # Walk each changed node back to its root. Compute the kickoff jobs once up
    # front rather than re-loading them on every recursive call.
    root_jobs = get_kickoff_jobs(instrument=None, reader=new_reader)
    root_nodes_to_reprocess = []
    for node in nodes:
        root_nodes_to_reprocess.extend(
            get_updated_root_node(node, nodes, new_reader, root_jobs)
        )

    jobs_to_reprocess = [
        (job.source, job.data_type, job.descriptor) for job in root_nodes_to_reprocess
    ]

    # A node can get found as the root more than once (e.g. if a pipeline fans in
    # from more than one changed branch), so dedupe before returning.
    return sorted(list(set(jobs_to_reprocess)))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "previous_checkout",
        type=Path,
        help="A checkout of the last deployed commit of this repository.",
    )
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as work_dir:
        old_yaml_dir = previous_yaml_dir(args.previous_checkout, Path(work_dir))
        logger.info(f"Comparing against the dependency YAML in {old_yaml_dir}")
        # The reader loads every file when it's created, so the temporary folder
        # can be removed afterwards.
        old_reader = DependencyConfigReader(old_yaml_dir)
    # With no folder given, the reader uses the YAML packaged with the installed
    # imap-data-access, which is what this deploy will run with.
    new_reader = DependencyConfigReader()

    # First validate the new dependency YAML. imap-data-access's CI already
    # validates it, but check again before acting on it.
    for job in get_kickoff_jobs(reader=new_reader):
        validate_dependency_yaml_versions(new_reader, job)

    reprocess_jobs = reprocess_jobs_for_major_version_bump(old_reader, new_reader)
    logger.info(f"Found {len(reprocess_jobs)} jobs to reprocess: {reprocess_jobs}")
    # Print as JSON dict so the github action can parse it.
    reprocess_jobs = [
        {"instrument": job[0], "data_level": job[1], "descriptor": job[2]}
        for job in reprocess_jobs
    ]
    print(json.dumps(reprocess_jobs))
