import logging
import sys

logger = logging.getLogger()
logger.setLevel(logging.INFO)
logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)


class DbIngestQuery:
    """
    Class to represent the connection to the ingest query.

    ...

    Attributes
    ----------
    query: str
        the query string created for ingest including placeholders for
        the ingest data.
    data: tuple
        data for the ingest query that will be inserted into the query
        string by psycopg2.
    s3_path: str
        S3 path for the file being ingested.
    metadata: dict
        metadata dictionary for the file being ingested.
    """

    def __init__(self, s3_path: str, metadata: dict):
        self.query: str
        self.data: tuple
        self.s3_path = s3_path
        self.metadata = metadata

        self._construct_ingestion_query()

    def _construct_ingestion_query(self):
        """Constructs the ingestion query string and corresponding data structure."""
        logger.info("Constructing metadata ingestion query")
        # add s3 path to the id field
        self.metadata["id"] = self.s3_path
        # break up date into year, month, day
        self.metadata["year"] = self.metadata["date"][:4]
        self.metadata["month"] = self.metadata["date"][4:6]
        self.metadata["day"] = self.metadata["date"][6:8]
        # remove date from metadata
        self.metadata.pop("date")
        logger.info(f"metadata: {self.metadata}")
        # create a string for the fields in query
        fields = ", ".join(self.metadata.keys())
        # create placeholders for the query data to put into query string
        placeholders = ", ".join(["%s"] * len(self.metadata))
        # format query using the field names and the correct number
        # of placeholders. The resulting query should look something
        # like this:
        #
        # INSERT INTO metadata (
        #    mission, instrument, level, id, year, month, day)
        # VALUES (%s, %s, %s, %s, %s, %s, %s)
        #
        # where each value in the data tuple should map to
        # one of the placeholders (%s) in the query string
        self.query = f"""
                    INSERT INTO metadata (
                        {fields})
                    VALUES ({placeholders})
                """
        logger.info(f"query: {self.query}")
        # create tuple to hold query data
        self.data = tuple(self.metadata.values())
        logger.info(f"query values: {self.data}")
