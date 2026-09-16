"""Validate the dependency YAML file."""

import numpy as np

from sds_data_manager.orchestration.dependency import (
    DependencyConfigReader,
    get_kickoff_jobs,
)
from sds_data_manager.orchestration.types import ProcessingJobNode


def validate_dependency_yaml_versions(
    reader, node: ProcessingJobNode | None, major_version: int | None = None
):
    """Validate the dependency YAML file.

    This function will raise an error if the dependency YAML file has
    an invalid major_version. The major versions should be monotonically increasing
    for each pipeline. A downstream job can not have a lower major version than
    an upstream job. E.g. if (swe, l1a, sci) has major version 2, (swe, l1b, sci) must
    have major version 2 or higher.

    reader : DependencyConfigReader
        An instance of DependencyConfigReader.
    node : ProcessingJobNode | None
        The node to validate.
    major_version : int or None
        The major version of the previous node. If None, we assume there is no
        previous node and the node supplied is the root node.
    """
    if node is None:
        return

    # If major_version is none, this means that we are at the root node.
    # Any major_version greater than zero is valid for the root node so
    # set the major_version to zero to simulate the previous node's
    # major_version
    if major_version is None:
        major_version = 0

    # First find any outputs that have identical descriptors. These products
    # must all have the same major_version.
    for descriptor, count in zip(
        *np.unique([out.descriptor for out in node.outputs], return_counts=True),
        strict=True,
    ):
        if count > 1:  # check if there are multiple outputs with the same descriptor
            matching_desc_versions = [
                out.major_version
                for out in node.outputs
                if out.descriptor == descriptor
            ]
            if (
                len(set(matching_desc_versions)) > 1
            ):  # check if all major versions are the same
                raise ValueError(
                    f"Invalid major version for job node outputs: {node.outputs}. All "
                    f"outputs with identical descriptors should have the same major "
                    f"versions. Found versions {matching_desc_versions}"
                )

    if not any([out.data_type == node.data_type for out in node.outputs]):
        raise ValueError(
            f"At least one output must have the same data level as the job node "
            f"itself. Job node level: {node.data_type}, output levels :"
            f" {[out.data_type for out in node.outputs]}"
        )
    # loop through each output of the node and check if the major version is valid
    for output in node.outputs:
        if output.major_version < major_version:
            raise ValueError(
                f"Output ({output.source}, {output.data_type}, {output.descriptor}) "
                f"has major_version {output.major_version}. It should be greater"
                f" than or equal to {major_version}"
            )
        # Get the processing job(s) that uses this dependency node
        processing_nodes = reader.get_nodes_for_input(output)
        # Validate each of the processing nodes recursively
        for processing_node in processing_nodes:
            if processing_node.source != node.source:
                # If the sources are different we should skip this check.
                continue
            validate_dependency_yaml_versions(
                reader, processing_node, output.major_version
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
