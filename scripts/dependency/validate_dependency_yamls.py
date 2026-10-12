"""Validate the dependency YAML file."""

import argparse
import logging
from abc import ABCMeta, abstractmethod
from pathlib import Path

import numpy as np

from scripts.dependency.utils import (
    get_nodes_to_check,
    get_updated_output_dependency_nodes,
)
from sds_data_manager.orchestration.dependency import (
    DependencyConfigReader,
    get_kickoff_jobs,
)
from sds_data_manager.orchestration.types import ProcessingJobNode

logger = logging.getLogger(__name__)


class DependencyCheck(metaclass=ABCMeta):
    """Base class for a validation check run on each dependency job node.

    Attributes
    ----------
    check_id : int
        Validation check ID.
    description : str
        Description of the rule the check enforces.
    """

    check_id: int
    description: str

    @abstractmethod
    def run_check(self, job_node: ProcessingJobNode, prev_major_version: int) -> None:
        """Run the check on a single job node.

        Parameters
        ----------
        job_node : ProcessingJobNode
            The job node to validate.
        prev_major_version : int
            The major version of the upstream output that feeds this job node,
            or 0 if this is the root node.

        Raises
        ------
        ValueError
            If the job node fails the check.
        """
        pass


class MajorVersionIncrementCheck(DependencyCheck):
    """Check that major versions never decrease down a pipeline.

    Parameters
    ----------
    nodes_to_check : set[tuple[str, str, str]] or None
        (source, data_type, descriptor) keys of the job nodes to check, e.g.
        from ``get_nodes_to_check``. If None, every job node is checked.
    """

    check_id = 0
    description = (
        "The major versions should be monotonically increasing for each pipeline."
        " A downstream job can not have a lower major version than an upstream "
        "job. E.g. if (swe, l1a, sci) has major version 2, (swe, l1b, sci) must"
        " have major version 2 or higher."
    )

    def __init__(self, nodes_to_check: set[tuple[str, str, str]] | None = None):
        """Initialize the major version increment check."""
        self.nodes_to_check = nodes_to_check

    def run_check(self, job_node: ProcessingJobNode, prev_major_version: int) -> None:
        """Raise if any output's major version is lower than the upstream one.

        Parameters
        ----------
        job_node : ProcessingJobNode
            The job node to validate.
        prev_major_version : int
            The major version of the upstream output that feeds this job node,
            or 0 if this is the root node.

        Raises
        ------
        ValueError
            If an output has a major version lower than ``prev_major_version``.
        """
        job_key = (job_node.source, job_node.data_type, job_node.descriptor)
        if self.nodes_to_check is not None and job_key not in self.nodes_to_check:
            return
        # loop through each output of the node and check if the major version is valid
        for output in job_node.outputs:
            if output.major_version < prev_major_version:
                raise ValueError(
                    f"Output ({output.source}, {output.data_type}, "
                    f"{output.descriptor}) has major_version "
                    f"{output.major_version}. It should be greater than or "
                    f"equal to {prev_major_version}"
                )


class IdenticalDescriptorCheck(DependencyCheck):
    """Check that outputs sharing a descriptor share a major version."""

    check_id = 1
    description = (
        "All outputs with identical descriptors should have the same major versions."
    )

    def run_check(self, job_node: ProcessingJobNode, prev_major_version: int) -> None:
        """Raise if outputs with the same descriptor have different major versions.

        Parameters
        ----------
        job_node : ProcessingJobNode
            The job node to validate.
        prev_major_version : int
            Unused by this check.

        Raises
        ------
        ValueError
            If outputs with identical descriptors have different major versions.
        """
        # First find any outputs that have identical descriptors. These products
        # must all have the same major_version.
        for descriptor, count in zip(
            *np.unique(
                [out.descriptor for out in job_node.outputs], return_counts=True
            ),
            strict=True,
        ):
            if (
                count > 1
            ):  # check if there are multiple outputs with the same descriptor
                matching_desc_versions = [
                    out.major_version
                    for out in job_node.outputs
                    if out.descriptor == descriptor
                ]
                if (
                    len(set(matching_desc_versions)) > 1
                ):  # check if all major versions are the same
                    raise ValueError(
                        f"Invalid major version for job node outputs: "
                        f"{job_node.outputs}. All outputs with identical descriptors"
                        f" should have the same major versions. Found versions "
                        f"{matching_desc_versions}."
                    )


class MatchingOutputNodeCheck(DependencyCheck):
    """Check that a job node produces at least one output at its own level."""

    check_id = 2
    description = (
        "At least one output must have the same data level as the job node itself."
    )

    def run_check(self, job_node: ProcessingJobNode, prev_major_version: int) -> None:
        """Raise if no output has the same data level as the job node.

        Parameters
        ----------
        job_node : ProcessingJobNode
            The job node to validate.
        prev_major_version : int
            Unused by this check.

        Raises
        ------
        ValueError
            If none of the outputs match the job node's data level.
        """
        if not any([out.data_type == job_node.data_type for out in job_node.outputs]):
            raise ValueError(
                f"At least one output must have the same data level as the job node "
                f"itself. Job node level: {job_node.data_type}, output levels :"
                f" {[out.data_type for out in job_node.outputs]}"
            )


# Map of check ID to check class, used to select checks from the command line.
CHECKS: dict[int, type[DependencyCheck]] = {
    check.check_id: check
    for check in (
        MajorVersionIncrementCheck,
        IdenticalDescriptorCheck,
        MatchingOutputNodeCheck,
    )
}


def validate_dependency_yaml_versions(
    reader,
    validation_checks: list[DependencyCheck],
    node: ProcessingJobNode | None,
    major_version: int | None = None,
):
    """Validate the dependency YAML file.

    Run each validation check on ``node``, then recurse into the downstream
    job nodes of the same source. Raises an error on the first failed check.

    Parameters
    ----------
    reader : DependencyConfigReader
        An instance of DependencyConfigReader.
    node : ProcessingJobNode | None
        The node to validate.
    validation_checks : list[DependencyCheck]
        The checks to run on each node.
    major_version : int or None
        The major version of the previous node. If None, we assume there is no
        previous node and the node supplied is the root node.

    Raises
    ------
    ValueError
        If any node fails a validation check.
    """
    if node is None:
        return

    # If major_version is none, this means that we are at the root node.
    # Any major_version greater than zero is valid for the root node so
    # set the major_version to zero to simulate the previous node's
    # major_version
    if major_version is None:
        major_version = 0

    # Run each specified check.
    for check in validation_checks:
        check.run_check(node, major_version)

    # Recursively validate output nodes
    for output in node.outputs:
        # Get the processing job(s) that uses this dependency node
        processing_nodes = reader.get_nodes_for_input(output)
        # Validate each of the processing nodes recursively
        for processing_node in processing_nodes:
            if processing_node.source != node.source:
                # If the sources are different we should skip this check.
                continue
            validate_dependency_yaml_versions(
                reader,
                validation_checks,
                processing_node,
                output.major_version,
            )


def main(argv: list[str] | None = None) -> None:
    """Run the main function."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--checks",
        type=int,
        nargs="+",
        choices=sorted(CHECKS),
        default=sorted(CHECKS),
        help="IDs of the checks to run. Defaults to all checks. "
        + " ".join(f"{i}: {check.__name__}." for i, check in CHECKS.items()),
    )
    parser.add_argument(
        "--previous-yaml-path",
        type=Path,
        default=None,
        help="Directory containing the previous dependency YAML files. Used "
        f"for check {MajorVersionIncrementCheck.check_id}. If not provided"
        f" {MajorVersionIncrementCheck.__name__} will not check the diff and run for"
        f" every node.",
    )
    args = parser.parse_args(argv)

    # Without specifying the yaml path, DependencyConfigReader will use the default
    # (in this case the current/new) dependency yamls
    reader = DependencyConfigReader()

    checks = []
    for check_id in args.checks:
        if (
            check_id == MajorVersionIncrementCheck.check_id
            and args.previous_yaml_path is not None
        ):
            # Only check the jobs that were updated in the code changes, plus the
            # jobs directly downstream of them, since only those can have a new
            # decrease.
            old_reader = DependencyConfigReader(args.previous_yaml_path)
            updated_nodes = get_updated_output_dependency_nodes(old_reader, reader)
            nodes_to_check = get_nodes_to_check(updated_nodes, reader)
            checks.append(MajorVersionIncrementCheck(nodes_to_check=nodes_to_check))
        else:
            checks.append(CHECKS[check_id]())

    # Get the root job of each pipeline and validate the yaml file
    # versions.
    kickoff_processing_jobs = get_kickoff_jobs()

    for job in kickoff_processing_jobs:
        try:
            validate_dependency_yaml_versions(reader, checks, job)
            print(f"Validated the {job.source} dependency YAML file")
        except ValueError as e:
            print(f"Invalid dependency file for {job.source}.")
            raise e


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
