"""Contains a registry of default job information."""

from sds_data_manager.orchestration.imap_job import IMAPJobHandler
from sds_data_manager.orchestration.types import ProcessingJobNode


class JobBuilderRegistry:
    """Encapsulate all custom IMAP Job Builders."""

    _registry = {}  # noqa: RUF012

    @classmethod
    def register(cls, source: str, data_type: str, descriptor: str):
        """Register a new job builder class."""

        def wrapper(wrapped_class):
            cls._registry[(source, data_type, descriptor)] = wrapped_class
            return wrapped_class

        return wrapper

    @classmethod
    def get_builder(cls, job_node: ProcessingJobNode):
        """Get the class that builds the Dagster handling for a specific job type."""
        key = (job_node.source, job_node.data_type, job_node.descriptor)
        # Returns the specific builder if it exists, otherwise the default
        builder_class = cls._registry.get(key, IMAPJobHandler)
        return builder_class(job_node)
