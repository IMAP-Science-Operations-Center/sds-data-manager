"""The dependency configuration for this deployment.

The node types and the reader for the dependency YAML files live in
``imap_data_access.dependencies``. The YAML files themselves still live in this
package, in ``dependencies/``, until they ship with imap-data-access. This module
points the reader at them by default, so callers can keep writing
``DependencyConfigReader()``.
"""

from pathlib import Path

from imap_data_access.dependencies import (
    DependencyConfigReader as BaseDependencyConfigReader,
)
from imap_data_access.dependencies import ProcessingJobNode, clear_config_cache
from imap_data_access.dependencies import get_kickoff_jobs as _get_kickoff_jobs

__all__ = ["DependencyConfigReader", "clear_config_cache", "get_kickoff_jobs"]


class DependencyConfigReader(BaseDependencyConfigReader):
    """Read the dependency YAML files shipped with this package."""

    def __init__(self, yaml_dir: Path | None = None):
        """Load the dependency configuration, or reuse it if already loaded.

        Parameters
        ----------
        yaml_dir : Path, optional
            Directory containing the ``dependencies/`` subfolder of instrument YAML
            files. Defaults to this module's directory. Pass a different directory
            to load the configuration from another checkout, e.g. to compare
            against an older revision.
        """
        super().__init__((yaml_dir or Path(__file__).parent) / "dependencies")


def get_kickoff_jobs(
    instrument: str | None = None,
    reader: BaseDependencyConfigReader | None = None,
) -> list[ProcessingJobNode]:
    """Return the jobs that kick off each instrument pipeline.

    See ``imap_data_access.dependencies.get_kickoff_jobs``. This version reads this
    package's dependency YAML files when no reader is given.
    """
    return _get_kickoff_jobs(instrument, reader or DependencyConfigReader())
