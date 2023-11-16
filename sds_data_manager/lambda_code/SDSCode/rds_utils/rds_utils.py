import json
import logging
import sys

import boto3
import psycopg2
from psycopg2 import Error

logger = logging.getLogger()
logger.setLevel(logging.INFO)
logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)


def connect_to_database(secret_name):
    session = boto3.session.Session()
    client = session.client(service_name="secretsmanager")
    secret_string = client.get_secret_value(SecretId=secret_name)["SecretString"]
    secret = json.loads(secret_string)
    logger.info("Attempting to connect with RDS database")
    connection = psycopg2.connect(
        host=secret["host"],
        database=secret["dbname"],
        user=secret["username"],
        password=secret["password"],
        port=secret["port"],
    )
    logger.info("Database connection successful")

    # Create a cursor object to interact with the database
    cursor = connection.cursor()

    return (connection, cursor)


def _construct_ingestion_query(s3_path, metadata):
    logger.info("Constructing metadata ingestion query")
    metadata["id"] = s3_path
    metadata["year"] = metadata["date"][:4]
    metadata["month"] = metadata["date"][4:6]
    metadata["day"] = metadata["date"][6:8]
    metadata.pop("date")
    logger.info(f"metadata: {metadata}")
    fields = ", ".join(metadata.keys())
    placeholders = ", ".join(["%s"] * len(metadata))

    insert_metadata_query = f"""
                INSERT INTO metadata (
                    {fields})
                VALUES ({placeholders})
            """
    logger.info(f"query: {insert_metadata_query}")
    data = tuple(metadata.values())
    logger.info(f"query values: {data}")

    return (insert_metadata_query, data)


def _close_database_connection(connection, cursor):
    # Close the cursor and connection
    if connection:
        connection.commit()
        cursor.close()
        connection.close()
        logger.info("PostgreSQL connection is closed.")


def write_metadata(metadata, s3_path, db_secret_name):
    try:
        connection, cursor = connect_to_database(db_secret_name)
        # Execute the create table query
        query, data = _construct_ingestion_query(s3_path, metadata)
        logger.info("Attempting to execute the query")
        cursor.execute(query, data)
        logger.info("Query executed successfully")
    except (Exception, Error) as error:
        logger.info(f"Error while connecting to PostgreSQL: {error}")

    finally:
        _close_database_connection(connection, cursor)
