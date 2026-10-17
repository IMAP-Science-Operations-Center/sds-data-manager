"""Determine jobs that need reprocessing due to a major version bump."""

import argparse
import json
import logging
from pathlib import Path

from scripts.dependency.utils import (
    get_updated_output_dependency_nodes,
    get_updated_root_node,
)
from scripts.dependency.validate_dependency_yamls import (
    validate_dependency_yaml_versions,
)
from sds_data_manager.orchestration.dependency import (
    DependencyConfigReader,
    get_kickoff_jobs,
)

logger = logging.getLogger(__name__)


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

    parser = argparse.ArgumentParser()
    parser.add_argument("previous_yaml_path")
    args = parser.parse_args()
    yaml_path = Path(args.previous_yaml_path)

    # Create a DependencyConfigReader using the previous dependency yaml files
    old_reader = DependencyConfigReader(yaml_path)
    # Without specifying the yaml path, DependencyConfigReader will use the default
    # (in this case the current/new) dependency yamls
    new_reader = DependencyConfigReader()

    # First validate new dependency yamls. They should already have been validated
    # from the github action in the PR creation but just to be sure.
    for job in get_kickoff_jobs():
        validate_dependency_yaml_versions(new_reader, job)

    reprocess_jobs = reprocess_jobs_for_major_version_bump(old_reader, new_reader)
    logger.info(f"Found {len(reprocess_jobs)} jobs to reprocess: {reprocess_jobs}")
    # Print as JSON dict so the github action can parse it.
    reprocess_jobs = [
        {"instrument": job[0], "data_level": job[1], "descriptor": job[2]}
        for job in reprocess_jobs
    ]
    print(json.dumps(reprocess_jobs))
