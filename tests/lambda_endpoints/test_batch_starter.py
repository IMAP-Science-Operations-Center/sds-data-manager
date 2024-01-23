from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from sds_data_manager.lambda_code.batch_starter import (
    append_attributes,
    extract_components,
    find_dependencies,
    load_data,
    prepare_data,
    query_instrument,
    query_upstream_dependencies,
)
from sds_data_manager.lambda_code.SDSCode.database.models import Base, FileCatalog


@pytest.fixture(scope="module")
def test_engine():
    return create_engine("sqlite:///:memory:")


@pytest.fixture(scope="module")
def test_session(test_engine):
    session_local = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    session = session_local()
    yield session
    session.close()


@pytest.fixture(scope="module")
def test_file_catalog_simulation(test_session):
    # Setup: Add a test record to the database
    test_record = FileCatalog(
        file_path="/path/to/file",
        instrument="ultra-45",
        data_level="l2",
        descriptor="science",
        start_date=datetime(2024, 1, 1),
        end_date=datetime(2024, 1, 2),
        version="v00-01",
        extension=".cdf",
        status_tracking_id=1,  # Assuming a valid ID from 'status_tracking' table
    )

    test_record_2 = FileCatalog(
        file_path="/path/to/file",
        instrument="hit",
        data_level="l0",
        descriptor="science",
        start_date=datetime(2024, 1, 1),
        end_date=datetime(2024, 1, 2),
        version="v00-01",
        extension=".cdf",
        status_tracking_id=1,  # Assuming a valid ID from 'status_tracking' table
    )
    test_session.add(test_record)
    test_session.add(test_record_2)
    test_session.commit()

    return test_session


def test_extract_components():
    "Tests extract_components function."
    filename = "imap_ultra-45_l2_science_20240101_20240102_v00-01.cdf"
    components = extract_components(filename)

    expected_components = {
        "instrument": "ultra-45",
        "datalevel": "l2",
        "descriptor": "science",
        "startdate": "20240101",
        "enddate": "20240102",
        "version": "v00-01",
    }

    assert components == expected_components


def test_query_instrument(test_file_catalog_simulation):
    "Tests query_instrument function."

    upstream_dependency = {"instrument": "ultra-45", "level": "l2", "version": "v00-01"}

    "Tests query_instrument function."
    record = query_instrument(
        test_file_catalog_simulation, upstream_dependency, "20240101", "20240102"
    )

    assert record.instrument == "ultra-45"
    assert record.data_level == "l2"
    assert record.version == "v00-01"
    assert record.start_date == datetime(2024, 1, 1)
    assert record.end_date == datetime(2024, 1, 2)


def test_append_attributes(test_file_catalog_simulation):
    "Tests append_attributes function."
    downstream_dependents = [{"instrument": "codicelo", "level": "l3b"}]

    complete_dependents = append_attributes(
        test_file_catalog_simulation,
        downstream_dependents,
        "20240101",
        "20240102",
        "v00-01",
    )

    expected_complete_dependent = {
        "instrument": "codicelo",
        "level": "l3b",
        "version": "v00-01",
        "start_date": "20240101",
        "end_date": "20240102",
    }

    assert complete_dependents[0] == expected_complete_dependent


def test_load_data():
    "Tests load_data function."
    base_directory = Path(__file__).resolve()
    base_path = base_directory.parents[2] / "sds_data_manager" / "lambda_code"
    filepath = base_path / "downstream_dependents.json"

    data = load_data(filepath)

    assert data["codicehi"]["l0"][0]["level"] == "l1a"


def test_find_dependencies():
    "Tests find_dependencies function."
    base_directory = Path(__file__).resolve()
    base_path = base_directory.parents[2] / "sds_data_manager" / "lambda_code"
    filepath = base_path / "downstream_dependents.json"

    data = load_data(filepath)

    upstream_dependencies = find_dependencies("codicelo", "l3b", "v00-01", data)

    expected_result = [
        {"instrument": "codicelo", "level": "l2", "version": "v00-01"},
        {"instrument": "codicelo", "level": "l3a", "version": "v00-01"},
        {"instrument": "mag", "level": "l2", "version": "v00-01"},
    ]

    assert upstream_dependencies == expected_result


def test_query_upstream_dependencies(test_file_catalog_simulation):
    "Tests query_upstream_dependencies function."
    base_directory = Path(__file__).resolve()
    filepath = (
        base_directory.parents[2]
        / "sds_data_manager"
        / "lambda_code"
        / "downstream_dependents.json"
    )

    data = load_data(filepath)

    downstream_dependents = [
        {
            "instrument": "hit",
            "level": "l1a",
            "version": "v00-01",
            "start_date": "20240101",
            "end_date": "20240102",
        },
        {
            "instrument": "hit",
            "level": "l3",
            "version": "v00-01",
            "start_date": "20240101",
            "end_date": "20240102",
        },
    ]

    result = query_upstream_dependencies(
        test_file_catalog_simulation, downstream_dependents, data, "bucket_name"
    )

    assert list(result[0].keys()) == ["filename", "prepared_data"]


def test_prepare_data():
    "Tests prepare_data function."

    upstream_dependencies = [{"instrument": "hit", "level": "l0", "version": "v00-01"}]

    prepared_data = prepare_data(
        "imap_hit_l1a_sci_20240101_20240102_v00-01.cdf",
        upstream_dependencies,
        "bucket_name",
    )

    expected_prepared_data = (
        "imap_cli --instrument hit --level l1a "
        "--filename 's3://bucket_name/imap/hit/l1a/2024/01/"
        "imap_hit_l1a_sci_20240101_20240102_v00-01.cdf' "
        "--dependency [{'instrument': 'hit', 'level': 'l0', 'version': 'v00-01'}]"
    )

    assert prepared_data == expected_prepared_data
