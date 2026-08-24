"""Validate the dependency YAML file."""

from sds_data_manager.orchestration.dependency import (
    DependencyConfigReader,
    get_kickoff_jobs,
)
from sds_data_manager.orchestration.types import ProcessingJobNode


def validate_dependency_yaml_versions(
    reader, major_version, node: ProcessingJobNode | None
):
    """Validate the dependency YAML file.

    This function will raise an error if the dependency YAML file has
    an invalid major_version. The major versions should be monotonically increasing
    for each pipeline. A downstream job can not have a lower major version than
    an upstream job. E.g. if (swe, l1a, sci) has major version 2, (swe, l1b, sci) must
    have major version 2 or higher.

    reader : DependencyConfigReader
        An instance of DependencyConfigReader.
    major_version : int
        The major version of the previous node.
    node : ProcessingJobNode | None
        The node to validate.
    """
    if node is None:
        return
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
                reader, output.major_version, processing_node
            )


if __name__ == "__main__":
    reader = DependencyConfigReader()
    # Get the root job of each pipeline and validate the yaml file
    # versions.
    kickoff_processing_jobs = get_kickoff_jobs()
    for job in kickoff_processing_jobs:
        try:
            validate_dependency_yaml_versions(reader, 0, job)
            print(f"Validated the {job.source} dependency YAML file")
        except ValueError as e:
            print(f"Invalid dependency file for {job.source}.")
            raise e
