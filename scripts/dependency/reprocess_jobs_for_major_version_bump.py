"""Determine jobs that need reprocessing due to a major version bump."""

import argparse
import json
import logging
from pathlib import Path

from imap_data_access import VALID_DATALEVELS

from scripts.dependency.validate_dependency_yamls import (
    validate_dependency_yaml_versions,
)
from sds_data_manager.orchestration.dependency import (
    DependencyConfigReader,
    get_kickoff_jobs,
)
from sds_data_manager.orchestration.types import ProcessingJobNode

logger = logging.getLogger(__name__)


def get_updated_output_dependency_nodes(
    old_reader, new_reader
) -> list[ProcessingJobNode]:
    """Find the jobs that need reprocessing due to major_version bump.

    This function will loop through every output in the new dependency yaml and
    compare it against the same output in the old dependency yaml. If the job is
    new, or one of its outputs is new, or an output's major_version increased, the
    job gets added to the list of jobs that need to be reprocessed. If a
    major_version ever decreased, this raises an error since major_version should
    never decrease.

    old_reader: DependencyConfigReader
        Reader built from the previous dependency yaml files.
    new_reader: DependencyConfigReader
        Reader built from the current dependency yaml files.

    """
    updated_nodes = []
    for key, new_node in new_reader.config.items():
        old_node = old_reader.config.get(key, None)
        if old_node is None and new_node not in updated_nodes:
            # job didn't exist before, so it needs to be reprocessed
            updated_nodes.append(new_node)
            continue
        for new_output in new_node.outputs:
            # find the same output in the old node, if it's there
            old_output = next(
                (
                    output
                    for output in old_node.outputs
                    if output.source == new_output.source
                    and output.data_type == new_output.data_type
                    and output.descriptor == new_output.descriptor
                ),
                None,
            )
            if old_output is None:
                # output is new, so this job needs to be reprocessed
                if new_node not in updated_nodes:
                    updated_nodes.append(new_node)
                continue
            elif new_output.major_version < old_output.major_version:
                raise ValueError(
                    f"The major version of output ({new_output.source}, "
                    f"{new_output.data_type}, {new_output.descriptor}) decreased "
                    f"from {old_output.major_version} to {new_output.major_version}. "
                    "The major version should never decrease."
                )
            elif new_output.major_version > old_output.major_version:
                # major_version got bumped, so it needs to be reprocessed
                if new_node not in updated_nodes:
                    updated_nodes.append(new_node)

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
    """
    root_nodes = []
    if node in root_jobs:
        return [node]

    for input_node in node.inputs:
        # not a real processing job upstream of this (e.g. spice, ancillary),
        # so there's nothing to walk further up
        # Only check inputs of the same source since major versions only get bumped
        # within the same source.
        if (
            input_node.data_type not in VALID_DATALEVELS
            or input_node.source != node.source
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

    parser = argparse.ArgumentParser()
    parser.add_argument("previous_yaml_path")
    args = parser.parse_args()
    yaml_path = Path(args.previous_yaml_path)

    # Create a DependencyConfigReader using the previous dependency yaml files
    old_reader = DependencyConfigReader(yaml_path)
    # Without specifying the yaml path, DependencyConfigReader will use the default
    # (in this case the current/new) dependency yamls
    new_reader = DependencyConfigReader()

    # # First validate new dependency yamls. They should already have been validated
    # # from the github action in the PR creation but just to be sure.
    for job in get_kickoff_jobs():
        validate_dependency_yaml_versions(new_reader, 0, job)

    reprocess_jobs = reprocess_jobs_for_major_version_bump(old_reader, new_reader)
    logger.info(f"Found {len(reprocess_jobs)} jobs to reprocess: {reprocess_jobs}")
    # Print as JSON dict so the github action can parse it.
    reprocess_jobs = [
        {"instrument": job[0], "data_level": job[1], "descriptor": job[2]}
        for job in reprocess_jobs
    ]
    print(json.dumps(reprocess_jobs))
