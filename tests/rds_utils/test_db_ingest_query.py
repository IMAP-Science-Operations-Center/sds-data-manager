import pytest

from sds_data_manager.lambda_code.SDSCode.rds_utils.db_ingest_query import DbIngestQuery


@pytest.fixture()
def create_db_query():
    file_path = "/fake/file/path/file.pkts"
    metadata = {
        "mission": "imap",
        "instrument": "swe",
        "level": "l1a",
        "date": "20231030",
    }
    return DbIngestQuery(file_path, metadata)


def test_db_ingest_init(create_db_query):
    """Test that DBIngestQuery object correctly initializes"""
    ## Arrange
    query_expected = """
                    INSERT INTO metadata (
                        mission, instrument, level, id, year, month, day)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """

    query_data_expected = (
        "imap",
        "swe",
        "l1a",
        "/fake/file/path/file.pkts",
        "2023",
        "10",
        "30",
    )

    ## Assert
    assert create_db_query.query == query_expected
    assert create_db_query.data == query_data_expected


def test_ingest_query(create_db_query):
    """Test that the ingest_query() function returns the correct query"""

    ## Arrange
    query_expected = """
                    INSERT INTO metadata (
                        mission, instrument, level, id, year, month, day)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """

    ## Assert
    assert create_db_query.get_query() == query_expected


def test_ingest_data(create_db_query):
    ## Arrange
    query_data_expected = (
        "imap",
        "swe",
        "l1a",
        "/fake/file/path/file.pkts",
        "2023",
        "10",
        "30",
    )

    ## Assert
    assert create_db_query.get_data() == query_data_expected
