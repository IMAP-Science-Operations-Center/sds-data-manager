"""Contains a registry of default job information."""

import re

from sds_data_manager.orchestration.dependency import DependencyConfigReader
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
    def register_descriptor_pattern(
        cls, source: str, data_type: str, descriptor_pattern: str
    ):
        """Register builders for descriptors matching a regex within one source/type.

        This expands a broad descriptor pattern (for example cadence fragments like
        ``3mo``) into concrete registry keys by scanning dependency config descriptors.
        Source and data_type must still match exactly.
        """
        reader = getattr(cls, "_dependency_reader", None) or DependencyConfigReader()
        cls._dependency_reader = reader

        def wrapper(wrapped_class):
            matches = False
            for node_source, node_type, descriptor in reader.config.keys():
                # Pattern-match only the descriptor, but keep source/type strict.
                if (
                    re.search(descriptor_pattern, descriptor)
                    and node_source == source
                    and node_type == data_type
                ):
                    key = (source, data_type, descriptor)
                    # Check if key already exists in the registry
                    if key in cls._registry:
                        raise ValueError(
                            f"Duplicate registration for key {key}. "
                            f"Existing class: {cls._registry[key].__name__}, "
                            f"New class: {wrapped_class.__name__}"
                        )
                    cls._registry[key] = wrapped_class
                    matches = True

            if not matches:
                raise ValueError(
                    f"No matches found for descriptor pattern '{descriptor_pattern}'"
                )
            return wrapped_class

        return wrapper

    @classmethod
    def get_builder(cls, job_node: ProcessingJobNode):
        """Get the class that builds the Dagster handling for a specific job type."""
        key = (job_node.source, job_node.data_type, job_node.descriptor)
        # Returns the specific builder if it exists, otherwise the default
        builder_class = cls._registry.get(key, IMAPJobHandler)
        return builder_class(job_node)
