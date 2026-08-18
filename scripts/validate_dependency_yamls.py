"""Validate the dependency YAML file."""

from sds_data_manager.orchestration.dependency import (
    DependencyConfigReader,
    get_kickoff_jobs,
)
from sds_data_manager.orchestration.types import ProcessingJobNode


def validate_dependency_yaml(reader, major_version, node: ProcessingJobNode | None):
    """Validate the dependency YAML file.

    dependency_yaml: str
        Path to the dependency YAML file.
    node: ProcessingJobNode
        The node to validate.

    Returns:
        bool: True if the YAML file is valid, False otherwise.
    """
    if node is None:
        return
    for output in node.outputs:
        if output.major_version < major_version:
            raise ValueError(
                f"Output ({output.source}, {output.data_type}, {output.descriptor}) "
                f"has major_version {output.major_version}. It should be greater"
                f" than or equal to {major_version}"
            )

        major_version = output.major_version
        validate_dependency_yaml(
            reader, major_version, reader.get_node_for_input(output)
        )


if __name__ == "__main__":
    reader = DependencyConfigReader()
    kickoff_processing_jobs = get_kickoff_jobs()
    for job in kickoff_processing_jobs:
        try:
            validate_dependency_yaml(reader, 0, job)
            print(f"Validated {job.source} dependency YAML file")
        except ValueError as e:
            print(f"Invalid dependency file for {job.source}.", e)
