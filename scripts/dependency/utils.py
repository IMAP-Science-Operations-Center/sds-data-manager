"""Shared helpers for comparing old and new dependency YAML files."""

import logging

from imap_data_access import VALID_DATALEVELS

from sds_data_manager.orchestration.types import ProcessingJobNode

logger = logging.getLogger(__name__)


def get_updated_output_dependency_nodes(
    old_reader, new_reader
) -> list[ProcessingJobNode]:
    """Find the jobs that got an updated major_version.

    This function will loop through every potential job's output in the new
    dependency yaml and compare it against the output of the old potential
    job in the old dependency yaml. If the job is new, or one of its outputs
    is new, or an output's major_version increased, the potential job's
    dependency information node is added to the list of jobs that got updated.

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
                # output is new.
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
                # major_version got bumped.
                if new_potential_job not in updated_nodes:
                    updated_nodes.append(new_potential_job)

    return updated_nodes


def get_nodes_to_check(
    updated_nodes: list[ProcessingJobNode], reader
) -> set[tuple[str, str, str]]:
    """Find the job nodes whose major versions need to be validated.

    A major version decrease is between a job and the job directly downstream
    of it, so a new decrease can only appear if one of those two jobs changed.
    This returns the updated jobs plus every same-source job that takes one of
    their outputs as an input.

    updated_nodes: list[ProcessingJobNode]
        Jobs that are new or had a major_version bump, from
        get_updated_output_dependency_nodes().
    reader: DependencyConfigReader
        Reader built from the current dependency yaml files, used to look up
        which jobs use a given output.

    Returns
    -------
    set[tuple[str, str, str]]
        (source, data_type, descriptor) keys of the jobs to validate.
    """
    nodes_to_check = set()
    for node in updated_nodes:
        nodes_to_check.add((node.source, node.data_type, node.descriptor))
        for output in node.outputs:
            for downstream_node in reader.get_nodes_for_input(output):
                # Major versions only need to increase within the same source.
                if downstream_node.source != node.source:
                    continue
                nodes_to_check.add(
                    (
                        downstream_node.source,
                        downstream_node.data_type,
                        downstream_node.descriptor,
                    )
                )
    return nodes_to_check


def get_updated_root_node(
    node, nodes, new_reader, root_jobs
) -> list[ProcessingJobNode]:
    """Walk upstream from a node to find the furthest upstream job that changed.

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
