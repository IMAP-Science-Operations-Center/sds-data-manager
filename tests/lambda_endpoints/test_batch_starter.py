import csv
import datetime
import json
import zoneinfo

import pytest

from sds_data_manager.lambda_images.instruments.batch_starter import (
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
    with open("../test-data/codicehi_event.json") as file:
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
        repointing_id, INTEGER,
    );
    """

    cursor.execute(sql_command)

    # Insert mock data:
    with open("../test-data/process_kickoff_test_data.csv") as f:
        reader = csv.DictReader(f)
        for row in reader:
            values_to_insert = [
                row["filename"] if row["filename"].strip() else None,
                row["instrument"] if row["instrument"].strip() else None,
                row["version"] if row["version"].strip() else None,
                row["level"] if row["level"].strip() else None,
                row["mode"] if row["mode"].strip() else None,
                None if row["date"].strip() in ["NULL", ""] else row["date"],
                None if row["ingested"].strip() in ["NULL", ""] else row["ingested"],
                int(row["mag_id"])
                if row["mag_id"].strip() and row["mag_id"].isdigit()
                else None,
                int(row["spice_id"])
                if row["spice_id"].strip() and row["spice_id"].isdigit()
                else None,
                int(row["parent_codicehi_id"])
                if row["parent_codicehi_id"].strip()
                and row["parent_codicehi_id"].isdigit()
                else None,
            ]

            cursor.execute(
                """
                INSERT INTO sdc.codicehi (
                filename, instrument, version, level,
                mode, date, ingested,
                mag_id, spice_id, parent_codicehi_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                tuple(values_to_insert),
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


def test_setup_database(database):
    # Create a cursor from the connection
    cursor = database.cursor()

    # Use the cursor to execute SQL and fetch results
    cursor.execute("SELECT COUNT(*) FROM sdc.codicehi")
    count = cursor.fetchone()[0]
    cursor.close()

    with open("../test-data/process_kickoff_test_data.csv") as f:
        row_count = len(f.readlines())

    assert count == row_count


def test_get_process_details(database):
    # Test that we query the instrument database properly.
    conn = database
    cur = conn.cursor()

    data_level, version_number, process_dates = get_process_details(
        cur, "CodiceHi", "imap_l3a_sci_codicehi_20230602_v01.cdf"
    )

    assert data_level == "l3a"
    assert version_number == 1
    assert process_dates == ["2023-05-31", "2023-06-01", "2023-06-02"]


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
            "filename": "imap_l2_sci_codicehi_20230531_v01.cdf",
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
            "filename": "imap_l0_sci_codicehi_20230531_v01.cdf",
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

    assert records[0]["filename"] == "imap_l3b_sci_codicehi_20230531_v01.cdf"

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
