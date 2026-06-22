"""Contains a registry of file handling information."""

from dagster import DynamicPartitionsDefinition

from sds_data_manager.orchestration.imap_file import IMAPScienceFileHandler
from sds_data_manager.orchestration.types import DependencyNode


class FileBuilderRegistry:
    """Encapsulate all custom IMAP File Builders."""

    _registry = {}  # noqa: RUF012

    @classmethod
    def register(cls, source: str, data_type: str, descriptor: str):
        """Register a new file builder class."""

        def wrapper(wrapped_class):
            cls._registry[(source, data_type, descriptor)] = wrapped_class
            return wrapped_class

        return wrapper

    @classmethod
    def get_builder(
        cls, dep_node: DependencyNode, partition: DynamicPartitionsDefinition
    ):
        """Get the class that builds the Dagster handling for a specific file type."""
        key = (dep_node.source, dep_node.data_type, dep_node.descriptor)
        # Returns the specific builder if it exists, otherwise the default
        builder_class = cls._registry.get(key, IMAPScienceFileHandler)
        return builder_class(dep_node, partition)
