"""Shared fixtures for tests/scripts."""

import pytest
from imap_data_access.dependencies import clear_config_cache


@pytest.fixture(autouse=True)
def _clear_dependency_cache():
    """Give every test here its own copy of the dependency configuration.

    DependencyConfigReader caches its parsed config process-wide, and these tests
    change the config objects they get back, so the cache is cleared going in and
    again afterwards so their changes do not leak into other test modules.
    """
    clear_config_cache()
    yield
    clear_config_cache()
