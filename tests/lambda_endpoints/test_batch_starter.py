import datetime
import zoneinfo

import pytest
from alchemy_mock.mocking import UnifiedAlchemyMagicMock
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_rds as rds

from sds_data_manager.lambda_code.batch_starter import (
    all_dependency_present,
    extract_components,
    get_downstream_dependents,
    prepare_data,
    query_instruments,
    query_upstream_dependencies,
)
from sds_data_manager.lambda_code.SDSCode.database.models import FileCatalogTable
from sds_data_manager.stacks.database_stack import SdpDatabase


@pytest.fixture(scope="module")
def database_stack(app, networking_stack, env):
    rds_size = "SMALL"
    rds_class = "BURSTABLE3"
    rds_storage = 200
    database_stack = SdpDatabase(
        app,
        "RDS",
        description="IMAP SDP database",
        env=env,
        vpc=networking_stack.vpc,
        rds_security_group=networking_stack.rds_security_group,
        engine_version=rds.PostgresEngineVersion.VER_15_3,
        instance_size=ec2.InstanceSize[rds_size],
        instance_class=ec2.InstanceClass[rds_class],
        max_allocated_storage=rds_storage,
        username="imap",
        secret_name="sdp-database-creds-rds",
        database_name="imapdb",
    )
    return database_stack


def test_extract_components():
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


def test_get_downstream_dependents(database_stack):
    instrument = "mag"
    datalevel = "l2"

    dependents = get_downstream_dependents(instrument, datalevel)

    expected_dependents = [
        {"instrument": "codicelo", "level": "l3b"},
        {"instrument": "codicehi", "level": "l3b"},
        {"instrument": "swe", "level": "l3"},
        {"instrument": "hit", "level": "l3"},
    ]

    assert dependents == expected_dependents


def test_query_upstream_dependencies(self):
    # Create a mock session
    session = UnifiedAlchemyMagicMock()

    # Mock the response for the specific query
    session.add(FileCatalogTable(id=1, filename="file1.cdf", level="l1b"))
    session.add(FileCatalogTable(id=2, filename="file2.cdf", level="l2a"))
    session.add(FileCatalogTable(id=3, filename="file3.cdf", level="l1b"))

    # Call the function you are testing
    records = query_instruments(
        session,
        1,
        [datetime(2023, 5, 31)],
        [{"instrument": "codicehi", "level": "l1b"}],
    )

    # Assert the results
    self.assertEqual(len(records), 2)  # it should return two records
    self.assertTrue(
        all(record.level == "l1b" for record in records)
    )  # all records should have level 'l1b'
    self.assertEqual(
        records[0].filename, "file1.cdf"
    )  # additional assertions as needed
    self.assertEqual(records[1].filename, "file3.cdf")


def test_setup_database(database):
    # Create a cursor from the connection
    cursor = database.cursor()

    # Use the cursor to execute SQL and fetch results
    cursor.execute("SELECT COUNT(*) FROM sdc.codicehi")
    count = cursor.fetchone()[0]
    cursor.close()

    assert count == 3


def test_get_process_details(database):
    # Test that we query the instrument database properly.
    conn = database
    cur = conn.cursor()

    data_level, version_number, process_dates = get_process_details(
        cur, "CodiceHi", "imap_codicehi_l3a_20230602_v01.cdf"
    )

    assert data_level == "l3a"
    assert version_number == 1
    assert process_dates == ["2023-06-01", "2023-06-02"]


def test_all_dependency_present():
    # Test items in dependencies are all present in result_list.
    dependencies_true = [
        {"instrument": "CodiceHi", "level": "l0"},
        {"instrument": "CodiceHi", "level": "l2"},
    ]
    dependencies_false = [
        {"instrument": "CodiceHi", "level": "l0"},
        {"instrument": "CodiceHi", "level": "l3"},
    ]

    result = [
        {
            "id": 6,
            "filename": "imap_codicehi_l2_20230531_v01.cdf",
            "instrument": "codicehi",
            "version": 1,
            "level": "l2",
            "mode": "NULL",
            "date": datetime.datetime(
                2023, 6, 2, 5, 45, tzinfo=zoneinfo.ZoneInfo(key="America/Denver")
            ),
            "ingested": datetime.datetime(
                2023, 6, 2, 5, 45, tzinfo=zoneinfo.ZoneInfo(key="America/Denver")
            ),
            "mag_id": 4,
            "spice_id": 6,
            "parent_codicehi_id": 3,
            "status": "INCOMPLETE",
        },
        {
            "id": 7,
            "filename": "imap_codicehi_l0_20230531_v01.ccsds",
            "instrument": "codicehi",
            "version": 1,
            "level": "l0",
            "mode": "NULL",
            "date": datetime.datetime(
                2023, 6, 2, 5, 45, tzinfo=zoneinfo.ZoneInfo(key="America/Denver")
            ),
            "ingested": datetime.datetime(
                2023, 6, 2, 5, 45, tzinfo=zoneinfo.ZoneInfo(key="America/Denver")
            ),
            "mag_id": 4,
            "spice_id": 6,
            "parent_codicehi_id": 3,
            "status": "INCOMPLETE",
        },
    ]

    assert all_dependency_present(result, dependencies_true)
    assert not all_dependency_present(result, dependencies_false)


def test_query_dependents(database):
    # Test to query the database to make certain dependents are
    # not already there.
    conn = database
    cur = conn.cursor()

    instrument_downstream = [
        {"instrument": "CodiceHi", "level": "l3b"},
        {"instrument": "CodiceHi", "level": "l3c"},
    ]
    process_dates = ["2023-05-31", "2023-06-01", "2023-06-02"]

    # Dependents that have been ingested for this date range.
    records = query_instruments(cur, 1, process_dates, instrument_downstream)

    assert records[0]["filename"] == "imap_codicehi_l3b_20230531_v01.cdf"

    # Since there are 2 instruments x 3 dates and one record to remove = 5
    output = remove_ingested(records, instrument_downstream, process_dates)

    assert len(output) == 5


def test_query_dependencies(database):
    # Test code that decides if we have sufficient dependencies
    # for each dependent to process.
    conn = database
    cur = conn.cursor()

    output = [
        {"instrument": "CodiceHi", "level": "l2", "date": "2023-05-31"},
        {"instrument": "CodiceHi", "level": "l2", "date": "2023-06-01"},
        {"instrument": "CodiceHi", "level": "l2", "date": "2023-06-02"},
    ]
    result = query_upstream_dependencies(cur, output, 1)

    assert result == [{"instrument": "CodiceHi", "level": "l2", "date": "2023-06-02"}]


def test_prepare_data():
    output = [
        {"instrument": "CodiceHi", "level": "l2", "date": "2023-05-31"},
        {"instrument": "CodiceHi", "level": "l3", "date": "2023-06-01"},
        {"instrument": "CodiceHi", "level": "l2", "date": "2023-06-02"},
        {"instrument": "CodiceLo", "level": "l2", "date": "2023-06-02"},
    ]

    input_data = prepare_data(output)

    grouped_list = {
        "CodiceHi": {"l2": ["2023-05-31", "2023-06-02"], "l3": ["2023-06-01"]},
        "CodiceLo": {"l2": ["2023-06-02"]},
    }

    assert grouped_list == input_data
