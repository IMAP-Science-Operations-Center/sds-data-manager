"""Validate the dependency YAML file."""

import logging
from abc import ABCMeta, abstractmethod

import numpy as np

from sds_data_manager.orchestration.dependency import (
    DependencyConfigReader,
    get_kickoff_jobs,
)
from sds_data_manager.orchestration.types import ProcessingJobNode

logger = logging.getLogger(__name__)


class DependencyCheck(metaclass=ABCMeta):
    """Base class for a validation check run on each dependency job node.

    Parameters
    ----------
    check_id : int
        Validation check ID.
    description : str
        Human-readable description of the rule the check enforces.
    """

    def __init__(self, check_id: int, description: str) -> None:
        """Initialize the check with its ID and description."""
        self.check_id: int = check_id  # validation check ID
        self.description: str = description  # validation check description

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
    """Check that major versions never decrease down a pipeline."""

    check_id = 0
    description = (
        "The major versions should be monotonically increasing for each pipeline."
        " A downstream job can not have a lower major version than an upstream "
        "job. E.g. if (swe, l1a, sci) has major version 2, (swe, l1b, sci) must"
        " have major version 2 or higher."
    )

    def __init__(self) -> None:
        """Initialize the major version increment check."""
        super().__init__(self.check_id, self.description)

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

    def __init__(self) -> None:
        """Initialize the identical descriptor check."""
        super().__init__(self.check_id, self.description)

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

    def __init__(self) -> None:
        """Initialize the matching output node check."""
        super().__init__(self.check_id, self.description)

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


def validate_dependency_yaml_versions(
    reader,
    node: ProcessingJobNode | None,
    validation_checks: list[DependencyCheck] | None = None,
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
    validation_checks : list[DependencyCheck] or None
        The checks to run on each node. If None, all checks are run:
        MajorVersionIncrementCheck, IdenticalDescriptorCheck, and
        MatchingOutputNodeCheck.
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

    if validation_checks is None:
        validation_checks = [
            MajorVersionIncrementCheck(),
            IdenticalDescriptorCheck(),
            MatchingOutputNodeCheck(),
        ]

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
                processing_node,
                validation_checks,
                output.major_version,
            )


if __name__ == "__main__":
    reader = DependencyConfigReader()
    # Get the root job of each pipeline and validate the yaml file
    # versions.
    kickoff_processing_jobs = get_kickoff_jobs()

    for job in kickoff_processing_jobs:
        try:
            validate_dependency_yaml_versions(reader, job)
            print(f"Validated the {job.source} dependency YAML file")
        except ValueError as e:
            print(f"Invalid dependency file for {job.source}.")
            raise e
