import json
import logging
import sys

import boto3
import psycopg2
from psycopg2 import Error

from .db_ingest_query import DbIngestQuery

logger = logging.getLogger()
logger.setLevel(logging.INFO)
logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)


class DbConnection:
    """
    Class to represent the connection to the RDS database.

    ...

    Attributes
    ----------
    secret_name: str
        the name of the secret in the AWS Secrets Manager
    connection: psycopg2.connection
        psycopg2 connection object to handle connection with
        PostgresSQL database instance.
    cursor: psycopg2.cursor
        psycopg2 cursor obejct that allows code to execute PostgreSQL
        commands in the database session.

    Methods
    -------
    send_query(query):
        sends query to the database.
    close():
        closes the database connection.
    """

    def __init__(self, secret_name: str):
        self.secret_name = secret_name
        self.connection: psycopg2.connection
        self.cursor: psycopg2.cursor
        self._connect_to_database()

    def _get_secret_string(self):
        """
        Gets secret from AWS SecretsManager.

        Returns
        -------
        dict
            Dictionary with secret fields
        """
        try:
            session = boto3.session.Session()
            client = session.client(service_name="secretsmanager")
            secret_string = client.get_secret_value(SecretId=self.secret_name)[
                "SecretString"
            ]
            secret = json.loads(secret_string)
            return secret
        except Exception as error:
            logger.info(f"Error while connecting to SecretsManager: {error}")

    def _connect_to_database(self):
        """
        Creates a connection to the PostgreSQL database.
        """
        try:
            # connect to boto3 secretsmanager to get secret
            secret = self._get_secret_string()
            # connect to DB
            self.connection = psycopg2.connect(
                host=secret["host"],
                database=secret["dbname"],
                user=secret["username"],
                password=secret["password"],
                port=secret["port"],
            )
            logger.info("Database connection successful")

            # Create a cursor object to interact with the database
            self.cursor = self.connection.cursor()

        except (Exception, Error) as error:
            logger.info(f"Error while connecting to PostgreSQL: {error}")

    def send_query(self, query: DbIngestQuery):
        """
        Sends query to the PostgreSQL database.

        Parameters
        ----------
        query : DbIngestQuery
            DBIngestQuery object for the query to send ot database
        """
        if self.connection:
            try:
                logger.info("Attempting to execute the query")
                self.cursor.execute(query.get_query(), query.get_data())
                logger.info("Query executed successfully")
                self.connection.commit()
            except (Exception, Error) as error:
                logger.info(f"Error while querying to PostgreSQL: {error}")

    def close(self):
        """
        Close connection to the PostgreSQL database.
        """
        if self.connection:
            self.cursor.close()
            self.connection.close()
            logger.info("PostgreSQL connection is closed.")
