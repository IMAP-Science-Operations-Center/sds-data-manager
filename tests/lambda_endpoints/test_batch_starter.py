import datetime
import json
import zoneinfo
from pathlib import Path

import pytest

from sds_data_manager.lambda_code.batch_starter import (
    all_dependency_present,
    get_filename_from_event,
    get_process_details,
    prepare_data,
    query_instruments,
    query_upstream_dependencies,
    remove_ingested,
)


@pytest.fixture(scope="session")
def mock_event():
    """Example of the type of event that will be passed to
    the instrument lambda (in our case batch_starter.py).
    """
    directory = Path(__file__).parent.parent / "test-data" / "codicehi_event.json"
    with open(directory) as file:
        event = json.load(file)
    return event


@pytest.fixture()
def database(postgresql):
    """Populate test database."""

    cursor = postgresql.cursor()
    cursor.execute("CREATE SCHEMA IF NOT EXISTS sdc;")

    # Drop the table if it exists, to start with a fresh table
    cursor.execute("DROP TABLE IF EXISTS sdc.codicehi;")
    # TODO: sync with actual database schema once it is created
    sql_command = """
    CREATE TABLE sdc.codicehi (
        -- Primary key
        id SERIAL PRIMARY KEY,

        -- Basic columns
        filename TEXT UNIQUE NOT NULL,
        instrument TEXT NOT NULL,
        version INTEGER NOT NULL,
        level TEXT NOT NULL,
        mode TEXT,
        date TIMESTAMP WITH TIME ZONE DEFAULT NULL,
        ingested TIMESTAMP WITH TIME ZONE DEFAULT (now() AT TIME ZONE 'UTC'),
        mag_id INTEGER,
        spice_id INTEGER,
        parent_codicehi_id INTEGER,
        pointing_id INTEGER
    );
    """

    cursor.execute(sql_command)

    hardcoded_data = [
        {
            "id": 2,
            "filename": "imap_codicehi_l1b_20230531_v01.cdf",
            "instrument": "codicehi",
            "version": 1,
            "level": "l1b",
            "mode": None,
            "date": "2023-05-31 14:45:00+03",
            "ingested": "2023-06-02 14:45:00+06",
            "mag_id": None,
            "codicelo_id": None,
            "spice_id": None,
            "parent_codicehi_id": 1,
            "pointing_id": 1,
        },
        {
            "id": 4,
            "filename": "imap_codicehi_l3a_20230602_v01.cdf",
            "instrument": "codicehi",
            "version": 1,
            "level": "l3a",
            "mode": None,
            "date": "2023-06-02 14:45:00+03",
            "ingested": "2023-06-02 14:45:00+10",
            "mag_id": None,
            "codicelo_id": None,
            "spice_id": None,
            "parent_codicehi_id": 3,
            "pointing_id": 1,
        },
        {
            "id": 5,
            "filename": "imap_codicehi_l3b_20230531_v01.cdf",
            "instrument": "codicehi",
            "version": 1,
            "level": "l3b",
            "mode": None,
            "date": "2023-05-31 14:45:00+03",
            "ingested": "2023-06-02 14:45:00+11",
            "mag_id": 4,
            "codicelo_id": None,
            "spice_id": None,
            "parent_codicehi_id": 4,
            "pointing_id": 1,
        },
    ]

    for row in hardcoded_data:
        cursor.execute(
            """
            INSERT INTO sdc.codicehi (
                filename, instrument, version, level, mode, date,
                ingested, mag_id, spice_id, parent_codicehi_id, pointing_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
            (
                row["filename"],
                row["instrument"],
                row["version"],
                row["level"],
                row["mode"],
                row["date"],
                row["ingested"],
                row.get("mag_id"),
                row.get("spice_id"),
                row.get("parent_codicehi_id"),
                row.get("pointing_id"),
            ),
        )

    # Committing the transaction
    postgresql.commit()
    cursor.close()

    # Yield the connection so tests can use it directly if needed
    yield postgresql

    # Cleanup: close the connection after tests
    postgresql.close()


def test_get_filename_from_event(mock_event):
    # Use mock event from the fixture
    filename = get_filename_from_event(mock_event)

    assert filename == "imap_codicehi_l3a_20230602_v01.cdf"

    mock_event = {"some_other_key": {"not_the_expected_structure": {}}}

    with pytest.raises(
        KeyError, match="Invalid event format: Unable to extract filename"
    ):
        get_filename_from_event(mock_event)


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
