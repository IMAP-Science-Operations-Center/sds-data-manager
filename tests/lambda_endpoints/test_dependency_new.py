"""Tests for dependency_new module.

This module provides unit tests for the DependencyConfigReader class used to
read and retrieve upstream dependencies from instrument YAML configuration files.
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock, mock_open, patch

import pytest

from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency_refactoring.dependency_new import (  # noqa: E501
    DependencyConfigReader,
    DependencyResolver,
)

from sds_data_manager.lambda_code.SDSCode.database.models import ScienceFiles

from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.utils import (
    DependencyNode,
    UpstreamDependencyNode,
    get_cadence_duration,
)

# Use a short list of instruments that have valid YAML files for testing
TEST_INSTRUMENTS = ["codice", "hi", "lo", "swe"]

MOCK_VALID_INSTRUMENTS = (
    "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas."
    "dependency_refactoring.dependency_new.VALID_INSTRUMENTS"
)


@pytest.fixture(autouse=True)
def mock_valid_instruments():
    """Automatically mock VALID_INSTRUMENTS for all tests."""
    with patch(
        MOCK_VALID_INSTRUMENTS,
        TEST_INSTRUMENTS,
    ):
        yield


# Tests for validate_node
def test_validate_node_valid_instrument():
    """Test validation of valid instrument nodes."""
    config = DependencyConfigReader()
    assert (
        config.validate_node(
            {
                "upstream_source": "codice",
                "upstream_data_type": "l1a",
                "upstream_descriptor": "all",
                "required": True,
                "kickoff_job": True,
            }
        )
        is True
    )
    assert (
        config.validate_node(
            {
                "upstream_source": "hi",
                "upstream_data_type": "l1b",
                "upstream_descriptor": "hi-counters-aggregated",
                "required": True,
                "kickoff_job": False,
            }
        )
        is True
    )


def test_validate_node_valid_spice():
    """Test validation of valid SPICE nodes."""
    config = DependencyConfigReader()
    assert (
        config.validate_node(
            {
                "upstream_source": "leapseconds",
                "upstream_data_type": "spice",
                "upstream_descriptor": "historical",
                "required": True,
                "kickoff_job": False,
            }
        )
        is True
    )


def test_validate_node_dict_valid():
    """Test validation of valid dict-formatted nodes."""
    config = DependencyConfigReader()
    # Dict with all required fields
    assert (
        config.validate_node(
            {
                "upstream_source": "codice",
                "upstream_data_type": "l1a",
                "upstream_descriptor": "all",
                "required": True,
                "kickoff_job": False,
            }
        )
        is True
    )


def test_validate_node_dict_with_defaults():
    """Test validation of dict nodes with default required/kickoff_job."""
    config = DependencyConfigReader()
    # Dict without optional fields should use defaults
    assert (
        config.validate_node(
            {
                "upstream_source": "leapseconds",
                "upstream_data_type": "spice",
                "upstream_descriptor": "historical",
            }
        )
        is True
    )


def test_validate_node_dict_with_date_range():
    """Test validation of dict nodes with date range."""
    config = DependencyConfigReader()
    assert (
        config.validate_node(
            {
                "upstream_source": "hi",
                "upstream_data_type": "l1b",
                "upstream_descriptor": "45sensor-goodtimes",
                "date_range": ["-3p", "3p"],
            }
        )
        is True
    )


def test_validate_node_dict_missing_required_key():
    """Test that dict missing required key raises ValueError."""
    config = DependencyConfigReader()
    with pytest.raises(ValueError, match="must contain keys"):
        config.validate_node(
            {"upstream_source": "codice", "upstream_descriptor": "all"}
        )


def test_validate_node_not_list_or_dict():
    """Test that non-dict raises ValueError."""
    config = DependencyConfigReader()
    with pytest.raises(ValueError, match=r"Node must be a dict|must contain keys"):
        config.validate_node("not_a_dict")


def test_validate_node_legacy_list_wrong_length():
    """Test that non-dict raises ValueError."""
    config = DependencyConfigReader()
    with pytest.raises(ValueError, match=r"Node must be a dict|must contain keys"):
        config.validate_node(
            {
                "upstream_source": "codice",
                "upstream_data_type": "l1a",
            }
        )


def test_validate_node_invalid_source():
    """Test that invalid source raises ValueError."""
    config = DependencyConfigReader()
    with pytest.raises(ValueError, match="Invalid data source"):
        config.validate_node(
            {
                "upstream_source": "invalid_source",
                "upstream_data_type": "l1a",
                "upstream_descriptor": "all",
                "required": True,
                "kickoff_job": True,
            }
        )


def test_validate_node_invalid_data_type():
    """Test that invalid data type raises ValueError."""
    config = DependencyConfigReader()
    with pytest.raises(ValueError, match="Invalid data type"):
        config.validate_node(
            {
                "upstream_source": "codice",
                "upstream_data_type": "invalid_type",
                "upstream_descriptor": "all",
                "required": True,
                "kickoff_job": True,
            }
        )


def test_validate_node_empty_descriptor():
    """Test that empty descriptor raises ValueError."""
    config = DependencyConfigReader()
    with pytest.raises(ValueError, match="non-empty string"):
        config.validate_node(
            {
                "upstream_source": "codice",
                "upstream_data_type": "l1a",
                "upstream_descriptor": "",
                "required": True,
                "kickoff_job": True,
            }
        )


def test_validate_node_dict_empty_descriptor():
    """Test that dict with empty descriptor raises ValueError."""
    config = DependencyConfigReader()
    with pytest.raises(ValueError, match="non-empty string"):
        config.validate_node(
            {
                "upstream_source": "codice",
                "upstream_data_type": "l1a",
                "upstream_descriptor": "",
            }
        )


def test_recursive_flatten_list():
    """Test that nested lists are flattened correctly."""
    config = DependencyConfigReader()
    nested_list = [1, [2, 3], [[4], 5]]
    assert config.recursive_flatten_list(nested_list) == [1, 2, 3, 4, 5]

    # Empty list
    assert config.recursive_flatten_list([]) == []
    # Single element list
    assert config.recursive_flatten_list([1]) == [1]
    # Flat list with no nesting
    assert config.recursive_flatten_list([1, 2, 3, 4]) == [1, 2, 3, 4]


def test_load_all_dependencies_all_instruments():
    """Test that we can load all instrument YAML files.

    It tests that we parse them into the expected config format.
    """
    config = DependencyConfigReader().config

    # Verify config is loaded and not empty
    assert len(config) > 0


@patch(
    MOCK_VALID_INSTRUMENTS,
    ["test-instrument"],
)
def test_load_all_dependencies_missing_file():
    """Test error when YAML file is missing.

    We test by overwriting VALID_INSTRUMENTS to include a non-existent instrument.
    """
    with pytest.raises(FileNotFoundError, match="not found"):
        DependencyConfigReader()


@patch(
    MOCK_VALID_INSTRUMENTS,
    ["codice"],
)
@patch(
    "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas."
    "dependency_refactoring.dependency_new.yaml.safe_load"
)
@patch("builtins.open", new_callable=mock_open)
def test_load_all_dependencies_empty_yaml(mock_file, mock_yaml_load):
    """Test error when YAML content is empty."""
    mock_yaml_load.return_value = None

    with patch(
        "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas."
        "dependency_refactoring.dependency_new.Path"
    ) as mock_path:
        mock_yaml_file = MagicMock()
        mock_yaml_file.exists.return_value = True
        mock_path.return_value.parent.__truediv__.return_value = mock_yaml_file

        with pytest.raises(ValueError, match="empty"):
            DependencyConfigReader()


# Tests for get_cadence_job


def test_get_cadence_job_1mo():
    """Test extracting 1mo cadence from descriptor."""
    result = get_cadence_duration("all-1mo")
    assert result == "1mo"


def test_get_cadence_job_3mo():
    """Test extracting 3mo cadence from descriptor."""
    result = get_cadence_duration("h90-ena-h-sf-nsp-full-hae-6deg-3mo")
    assert result == "3mo"


def test_get_cadence_job_6mo():
    """Test extracting 6mo cadence from descriptor."""
    result = get_cadence_duration("h90-ena-h-hf-nsp-full-hae-6deg-6mo")
    assert result == "6mo"


def test_get_cadence_job_1yr():
    """Test extracting 1yr cadence from descriptor."""
    result = get_cadence_duration("h90-ena-h-sf-nsp-ram-hae-6deg-1yr")
    assert result == "1yr"


def test_get_cadence_job_no_cadence():
    """Test descriptor with no cadence indicator returns None."""
    result = get_cadence_duration("sci")
    assert result is None


def test_get_cadence_job_empty_descriptor():
    """Test empty descriptor returns None."""
    result = get_cadence_duration("")
    assert result is None


# Tests for get_downstream_dependency_nodes


def test_get_downstream_dependency_nodes_single():
    """Test getting downstream dependencies for science or ancillary file ingestions."""
    dependency_resolver = DependencyResolver()

    result = dependency_resolver.get_downstream_dependency_nodes(
        DependencyNode(source="swe", data_type="l1a", descriptor="sci")
    )

    assert len(result) == 1
    assert DependencyNode(source="swe", data_type="l1b", descriptor="sci") in result

    result = dependency_resolver.get_downstream_dependency_nodes(
        DependencyNode(
            source="hit",
            data_type="ancillary",
            descriptor="l1b-to-l2-standard-dt0-factors",
        )
    )

    assert len(result) == 1
    assert (
        DependencyNode(source="hit", data_type="l2", descriptor="standard-intensity")
        in result
    )


def test_get_downstream_dependency_nodes_multiple():
    """Test getting multiple downstream dependencies for a science file ingestion."""
    dependency_resolver = DependencyResolver()

    result = dependency_resolver.get_downstream_dependency_nodes(
        DependencyNode(source="hit", data_type="l1a", descriptor="counts-standard")
    )

    assert len(result) == 2
    assert (
        DependencyNode(source="hit", data_type="l1b", descriptor="standard-rates")
        in result
    )
    assert (
        DependencyNode(source="hit", data_type="l1b", descriptor="summed-rates")
        in result
    )


def test_get_downstream_dependency_nodes_no_dependents():
    """Test when a node has no downstream dependencies."""
    dependency_resolver = DependencyResolver()
    # Overwrite config
    dependency_config = {
        ("swe", "l1b", "swe-all"): [
            {
                "upstream_source": "swe",
                "upstream_data_type": "l1a",
                "upstream_descriptor": "all",
                "required": True,
                "kickoff_job": True,
            }
        ],
        ("mag", "l1b", "mag-all"): [
            {
                "upstream_source": "mag",
                "upstream_data_type": "l1a",
                "upstream_descriptor": "all",
                "required": True,
                "kickoff_job": True,
            }
        ],
    }
    dependency_resolver._config = dependency_config

    result = dependency_resolver.get_downstream_dependency_nodes(
        DependencyNode(source="codice", data_type="l2", descriptor="all")
    )

    assert len(result) == 0


def test_get_downstream_dependency_nodes_shared_dependency():
    """Test when multiple nodes depend on a shared upstream node such as SPICE data."""
    dependency_resolver = DependencyResolver()
    # Overwrite config
    dependency_config = {
        ("swe", "l1b", "swe-sci"): [
            {
                "upstream_source": "leapseconds",
                "upstream_data_type": "spice",
                "upstream_descriptor": "historical",
                "required": True,
                "kickoff_job": True,
            },
            {
                "upstream_source": "swe",
                "upstream_data_type": "l1a",
                "upstream_descriptor": "all",
                "required": True,
                "kickoff_job": True,
            },
        ],
        ("mag", "l1b", "mag-sci"): [
            {
                "upstream_source": "leapseconds",
                "upstream_data_type": "spice",
                "upstream_descriptor": "historical",
                "required": True,
                "kickoff_job": True,
            },
            {
                "upstream_source": "mag",
                "upstream_data_type": "l1a",
                "upstream_descriptor": "all",
                "required": True,
                "kickoff_job": True,
            },
        ],
        ("hi", "l1b", "hi-sci"): [
            {
                "upstream_source": "leapseconds",
                "upstream_data_type": "spice",
                "upstream_descriptor": "historical",
                "required": True,
                "kickoff_job": True,
            },
            {
                "upstream_source": "hi",
                "upstream_data_type": "l1a",
                "upstream_descriptor": "all",
                "required": True,
                "kickoff_job": True,
            },
        ],
    }
    dependency_resolver._config = dependency_config

    result = dependency_resolver.get_downstream_dependency_nodes(
        DependencyNode(source="leapseconds", data_type="spice", descriptor="historical")
    )

    assert len(result) == 3
    assert DependencyNode(source="swe", data_type="l1b", descriptor="swe-sci") in result
    assert DependencyNode(source="mag", data_type="l1b", descriptor="mag-sci") in result
    assert DependencyNode(source="hi", data_type="l1b", descriptor="hi-sci") in result


def test_get_downstream_dependency_nodes_ancillary_files():
    """Test ancillary files as upstream dependency that kickoff processing jobs.

    Realistic scenario where a single ancillary file from an instrument can trigger
    one or multiple downstream jobs at different processing levels. For example,
    a calibration LUT ingested can trigger l1a processing, which then enables l1b
    and potentially l2 processing downstream.
    """
    dependency_resolver = DependencyResolver()
    # Overwrite config
    dependency_config = {
        # Single ancillary LUT triggers only one l1a job
        ("codice", "l1a", "all"): [
            {
                "upstream_source": "codice",
                "upstream_data_type": "ancillary",
                "upstream_descriptor": "l1a-lut",
                "required": True,
                "kickoff_job": True,
            }
        ],
        # Single ancillary calibration triggers multiple l1b descriptor variants
        ("codice", "l1b", "lo-counters"): [
            {
                "upstream_source": "codice",
                "upstream_data_type": "l1a",
                "upstream_descriptor": "all",
                "required": True,
                "kickoff_job": True,
            },
            {
                "upstream_source": "codice",
                "upstream_data_type": "ancillary",
                "upstream_descriptor": "l1b-calibration",
                "required": True,
                "kickoff_job": True,
            },
        ],
        ("codice", "l1b", "hi-counters"): [
            {
                "upstream_source": "codice",
                "upstream_data_type": "l1a",
                "upstream_descriptor": "all",
                "required": True,
                "kickoff_job": True,
            },
            {
                "upstream_source": "codice",
                "upstream_data_type": "ancillary",
                "upstream_descriptor": "l1b-calibration",
                "required": True,
                "kickoff_job": True,
            },
        ],
        ("codice", "l1b", "hk"): [
            {
                "upstream_source": "codice",
                "upstream_data_type": "l1a",
                "upstream_descriptor": "all",
                "required": True,
                "kickoff_job": True,
            },
            {
                "upstream_source": "codice",
                "upstream_data_type": "ancillary",
                "upstream_descriptor": "l1b-calibration",
                "required": True,
                "kickoff_job": True,
            },
        ],
        # Single ancillary background model triggers multiple l2 descriptor variants
        ("ultra", "l2", "ultra-sci-1mo"): [
            {
                "upstream_source": "ultra",
                "upstream_data_type": "l2",
                "upstream_descriptor": "all",
                "required": True,
                "kickoff_job": True,
            },
            {
                "upstream_source": "ultra",
                "upstream_data_type": "ancillary",
                "upstream_descriptor": "l2-background",
                "required": False,
                "kickoff_job": True,
            },
        ],
        ("ultra", "l2", "ultra-sci-3mo"): [
            {
                "upstream_source": "ultra",
                "upstream_data_type": "l2",
                "upstream_descriptor": "all",
                "required": True,
                "kickoff_job": True,
            },
            {
                "upstream_source": "ultra",
                "upstream_data_type": "ancillary",
                "upstream_descriptor": "l2-background",
                "required": False,
                "kickoff_job": True,
            },
        ],
        # Different instruments with similar pattern
        ("hi", "l1a", "all"): [
            {
                "upstream_source": "hi",
                "upstream_data_type": "ancillary",
                "upstream_descriptor": "l1a-lut",
                "required": True,
                "kickoff_job": True,
            }
        ],
        ("hi", "l1b", "hi-all"): [
            {
                "upstream_source": "hi",
                "upstream_data_type": "l1a",
                "upstream_descriptor": "all",
                "required": True,
                "kickoff_job": True,
            },
            {
                "upstream_source": "hi",
                "upstream_data_type": "ancillary",
                "upstream_descriptor": "l1b-calibration",
                "required": True,
                "kickoff_job": True,
            },
        ],
    }
    dependency_resolver._config = dependency_config

    # Single ancillary file kicking off single l1a job
    codice_lut_jobs = dependency_resolver.get_downstream_dependency_nodes(
        DependencyNode(source="codice", data_type="ancillary", descriptor="l1a-lut")
    )
    assert len(codice_lut_jobs) == 1
    assert (
        DependencyNode(source="codice", data_type="l1a", descriptor="all")
        in codice_lut_jobs
    )

    # Single ancillary calibration file triggering multiple l1b jobs
    # (different descriptor variants of same level)
    codice_cal_jobs = dependency_resolver.get_downstream_dependency_nodes(
        DependencyNode(
            source="codice", data_type="ancillary", descriptor="l1b-calibration"
        )
    )
    assert len(codice_cal_jobs) == 3
    assert (
        DependencyNode(source="codice", data_type="l1b", descriptor="lo-counters")
        in codice_cal_jobs
    )
    assert (
        DependencyNode(source="codice", data_type="l1b", descriptor="hi-counters")
        in codice_cal_jobs
    )
    assert (
        DependencyNode(source="codice", data_type="l1b", descriptor="hk")
        in codice_cal_jobs
    )

    # Single ancillary background model triggering multiple l2 cadence jobs
    bg_model_jobs = dependency_resolver.get_downstream_dependency_nodes(
        DependencyNode(
            source="ultra", data_type="ancillary", descriptor="l2-background"
        )
    )
    assert len(bg_model_jobs) == 2
    assert (
        DependencyNode(source="ultra", data_type="l2", descriptor="ultra-sci-1mo")
        in bg_model_jobs
    )
    assert (
        DependencyNode(source="ultra", data_type="l2", descriptor="ultra-sci-3mo")
        in bg_model_jobs
    )
    # Verify different instrument ancillary also works
    hi_cal_jobs = dependency_resolver.get_downstream_dependency_nodes(
        DependencyNode(source="hi", data_type="ancillary", descriptor="l1b-calibration")
    )
    assert len(hi_cal_jobs) == 1
    assert (
        DependencyNode(source="hi", data_type="l1b", descriptor="hi-all") in hi_cal_jobs
    )


def test_get_downstream_dependency_nodes_with_date_ranges():
    """Test downstream lookup with dependencies that include date ranges.

    Complex scenario where upstream dependencies include optional time range fields
    (past_range, future_range) with format specifiers:
    - p: pointing
    - h: hourly
    - d: days
    - l: last_processed

    A single downstream product can depend on multiple upstream products with
    varying tuple lengths and time range formats.
    """
    dependency_resolver = DependencyResolver()
    # Overwrite config
    dependency_config = {
        # Downstream product with mixed upstream dependency types
        ("hi", "l1b", "45sensor-goodtimes"): [
            # Dependency with pointing-based time range:
            # 3 pointings past to 3 pointings future
            {
                "upstream_source": "hi",
                "upstream_data_type": "l1b",
                "upstream_descriptor": "45sensor-de",
                "required": True,
                "kickoff_job": True,
                "date_range": ["-3p", "3p"],
            },
            # Dependency without time range
            {
                "upstream_source": "hi",
                "upstream_data_type": "l1b",
                "upstream_descriptor": "45sensor-hk",
                "required": True,
                "kickoff_job": True,
            },
            # Another dependency without time range
            {
                "upstream_source": "hi",
                "upstream_data_type": "l1a",
                "upstream_descriptor": "45sensor-diagfee",
                "required": True,
                "kickoff_job": True,
            },
            # SPICE dependency (typically no time range)
            {
                "upstream_source": "leapseconds",
                "upstream_data_type": "spice",
                "upstream_descriptor": "historical",
                "required": True,
                "kickoff_job": False,
            },
        ],
        ("hi", "l1b", "45sensor-de"): [
            {
                "upstream_source": "hi",
                "upstream_data_type": "l1a",
                "upstream_descriptor": "45sensor-de",
                "required": True,
                "kickoff_job": True,
            },
            {
                "upstream_source": "hi",
                "upstream_data_type": "l1b",
                "upstream_descriptor": "45sensor-goodtimes",
                "required": True,
                "kickoff_job": True,
            },
            {
                "upstream_source": "leapseconds",
                "upstream_data_type": "spice",
                "upstream_descriptor": "historical",
                "required": True,
                "kickoff_job": False,
            },
        ],
    }
    dependency_resolver._config = dependency_config

    # Test downstream lookup for goodtimes product
    de_downstream = dependency_resolver.get_downstream_dependency_nodes(
        DependencyNode(source="hi", data_type="l1b", descriptor="45sensor-de")
    )

    # Verify it has 1 downstream dependency
    assert len(de_downstream) == 1
    assert (
        DependencyNode(source="hi", data_type="l1b", descriptor="45sensor-goodtimes")
        in de_downstream
    )


def test_upstream_dependency(session):
    """Test getting upstream dependencies for HIT L2 product.

    This test uses hit's (l2, macropixel-intensity) as an example.
    According to imap_hit_dependencies.yaml, it depends on:
      - [hit, l1b, sectored-rates, true, true]
      - [hit, ancillary, l1b-to-l2-macropixel-dt0-factors, true, true]
      - [hit, ancillary, l1b-to-l2-macropixel-dt1-factors, true, true]
      - [hit, ancillary, l1b-to-l2-macropixel-dt2-factors, true, true]
      - [hit, ancillary, l1b-to-l2-macropixel-dt3-factors, true, true]
    """
    # Create HIT L1B sectored-rates science file record (required upstream dependency)
    l1b_science_file = ScienceFiles(
        file_path="imap/hit/l1b/2026/03/imap_hit_l1b_sectored-rates_20260318_v001.cdf",
        instrument="hit",
        data_level="l1b",
        descriptor="sectored-rates",
        start_date=datetime(2026, 3, 18, 0, 0, 0, tzinfo=timezone.utc),
        version="v001",
        ingestion_date=datetime(2026, 3, 18, 12, 0, 0, tzinfo=timezone.utc),
        extension="cdf",
    )

    # Create HIT ancillary calibration factor files (required upstream dependencies)
    ancillary_files = []
    for factor_num in range(4):
        ancillary = ScienceFiles(
            file_path=f"imap/hit/ancillary/2026/03/imap_hit_ancillary_l1b-to-l2-macropixel-dt{factor_num}-factors_20260318_v001.cdf",
            instrument="hit",
            data_level="ancillary",
            descriptor=f"l1b-to-l2-macropixel-dt{factor_num}-factors",
            start_date=datetime(2026, 3, 18, 0, 0, 0, tzinfo=timezone.utc),
            version="v001",
            ingestion_date=datetime(2026, 3, 18, 12, 0, 0, tzinfo=timezone.utc),
            extension="cdf",
        )
        ancillary_files.append(ancillary)

    # Add all records to database
    session.add(l1b_science_file)
    session.add_all(ancillary_files)
    session.commit()

    # Create upstream dependency node for HIT L2 macropixel-intensity product
    upstream_node = UpstreamDependencyNode(
        source="hit",
        data_type="l2",
        descriptor="macropixel-intensity",
        start_date=datetime(2026, 3, 18, 0, 0, 0, tzinfo=timezone.utc),
        end_date=datetime(2026, 3, 18, 23, 59, 59, tzinfo=timezone.utc),
    )

    # Create resolver and call get_upstream_dependency
    resolver = DependencyResolver()
    result = resolver.get_upstream_dependency(session, upstream_node)

    # Verify result structure
    assert isinstance(result, dict)
    assert "status" in result
    assert "message" in result
    assert "data" in result

    # Verify we found the upstream dependencies (status 200)
    # With records in DB, should successfully find them
    assert result["status"] == 200, (
        f"Expected status 200, got {result['status']}: {result['message']}"
    )

    # # Verify the data structure contains serialized ProcessingInputCollection
    # assert isinstance(result["data"], dict), (
    #     f"Expected data to be dict, got {type(result['data'])}"
    # )

    # expected_serialized_data = {}
    # # Verify data contains expected structure from serialized
    # # ProcessingInputCollection. Should have keys for different input types
    # # (science_inputs, ancillary_inputs, etc.)
    # assert result["data"] == expected_serialized_data
