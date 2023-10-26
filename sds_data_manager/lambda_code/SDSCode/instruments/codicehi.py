"""
Lambda runtime code that triggers off of the arrival of data into an S3 bucket.
"""
import json
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

import boto3
import psycopg2

# Setup the logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create a Step Functions client
step_function_client = boto3.client("stepfunctions")


def get_filename_from_event(event):
    """
    Extracts the filename (object key) from the given S3 event
    without folder path.

    Parameters
    ----------
    event : dict
        The JSON formatted S3 event.

    Returns
    -------
    filename : str
        Extracted filename from the event.

    Raises
    ------
    KeyError:
        If the necessary fields are not found in the event.
    """
    try:
        full_path = event["detail"]["object"]["key"]
        return full_path.split("/")[-1]
    except KeyError as err:
        raise KeyError("Invalid event format: Unable to extract filename") from err


def db_connect(secret_arn: Optional[str] = None):
    """
    Retrieves secrets and connects to database.

    Parameters
    ----------
    secret_arn : str, optional
        The ARN for the database secrets in AWS Secrets Manager.

    Returns
    -------
    conn : psycopg.Connection
        Database connection.
    """
    client = boto3.client("secretsmanager")

    # TODO: we need to get the secret arn somehow; possibly
    #  passing it in from the event.

    try:
        response = client.get_secret_value(SecretId=secret_arn)
        secret_string = response["SecretString"]
        secret = json.loads(secret_string)
    except Exception as e:
        raise Exception(f"Error retrieving secret: {e}") from e

    try:
        conn = psycopg2.connect(
            dbname=secret["dbname"],
            user=secret["user"],
            password=secret["password"],
            host=secret["host"],
            port=secret["port"],
        )
    except Exception as e:
        raise Exception(f"Error connecting to the database: {e}") from e

    return conn


def get_process_details(cur, instrument, filename, process_range=7):
    """
    Gets details for instrument listed in event.

    Parameters
    ----------
    cur : psycopg2.extensions.cursor
        A psycopg2 database cursor object to execute
        database operations.
    instrument : str
        The name of the instrument for which details
        are to be retrieved.
    filename : str
        The filename associated with the instrument.
    process_range : int
        Numbers of days to process

    Returns
    -------
    level : str
        Instrument level
    version : int
        Version
    process_dates : list
        Dates to process
    """

    # Ensure the instrument value is safe to use in SQL.
    if not instrument.isidentifier():
        raise ValueError(f"Invalid instrument name: {instrument}")

    query = f"""SELECT * FROM sdc.{instrument.lower()}
                WHERE filename = %s
                LIMIT 1;"""  # Changed the limit to 1
    params = (filename,)

    cur.execute(query, params)
    column_names = [desc[0] for desc in cur.description]
    records = cur.fetchall()

    if not records:
        raise ValueError(f"No records found for filename: {filename}")

    record_dict = dict(zip(column_names, records[0]))

    level = record_dict["level"]
    version = record_dict["version"]

    dt = record_dict["date"]
    dt_start = dt - timedelta(days=process_range)

    process_dates = [dt_start.strftime("%Y-%m-%d"), dt.strftime("%Y-%m-%d")]

    return level, version, process_dates


def query_dependents(cur, version, process_dates, instrument_dependents):
    """
    Queries the database for dependent instruments and retrieves their records.

    Parameters
    ----------
    cur : psycopg2.extensions.cursor
        A psycopg2 database cursor object to execute database operations.
    version : int
        Version of the instrument to be queried.
    process_dates : list
        A list containing start and end date to filter records on their ingestion date.
    instrument_dependents : list of dict
        A list containing dictionaries of dependent instruments and their levels.
        Each dictionary should have keys 'instrument' and 'level'.

    Returns
    -------
    all_records : list of dict
        A list of dictionaries where each dictionary corresponds to a record
        from the database that matches the given criteria.

    """
    all_records = []

    # Loop through instrument dependents and query them
    for instrument in instrument_dependents:
        # Ensure the instrument name is valid
        if not instrument["instrument"].isidentifier():
            raise ValueError(
                f"Invalid instrument name in dependents: {instrument['instrument']}"
            )

        query = f"""SELECT * FROM sdc.{instrument['instrument'].lower()}
                    WHERE version = %s
                    AND level = %s
                    AND ingested BETWEEN %s::DATE AND (%s::DATE + INTERVAL '1 DAY')
                    LIMIT 100;"""
        params = (
            version,
            instrument["level"].lower(),
            min(process_dates),
            max(process_dates),
        )

        cur.execute(query, params)
        column_names = [desc[0] for desc in cur.description]
        records = cur.fetchall()

        # Map the column names to the records
        records_dicts = [dict(zip(column_names, record)) for record in records]

        all_records.extend(records_dicts)

    return all_records


def remove_ingested(records, dependents_level, process_dates):
    """
    Identifies and returns a list of dependent instruments
    that have not been ingested for the specified dates.

    Parameters
    ----------
    records : list of dict
        A list of dictionaries where each dictionary
        corresponds to a record from the database.
    dependents_level : list of dict
        A list containing dictionaries of dependent
        instruments and their levels.
    process_dates : list
        A list of date strings representing the dates
        to be checked for missing ingestions.

    Returns
    -------
    output : list of dict
        A list of dictionaries where each dictionary
        indicates a dependent instrument and its level
        that has not been ingested for a given date.

    """

    output = []

    records_set = {
        (rec["date"].date(), rec["instrument"], rec["level"]) for rec in records
    }

    for dependent in dependents_level:
        for date_str in process_dates:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()

            if (
                date_obj,
                dependent["instrument"].lower(),
                dependent["level"].lower(),
            ) not in records_set:
                output.append(
                    {
                        "instrument": dependent["instrument"],
                        "level": dependent["level"],
                        "date": date_str,
                    }
                )

    return output


def query_dependencies(cur, grouped_records, version_number):
    """
    Queries and checks dependencies for a list of grouped records.

    Parameters
    ----------
    cur : database cursor
        The cursor object associated with the database connection.
    grouped_records : list of dict
        A list of dictionaries where each dictionary corresponds to a record
        from the database with keys 'instrument', 'level', and 'date'.
    version_number : int or str
        The version number to be used when querying dependent records.

    Returns
    -------
    instruments_to_process : list of dict
        A list of dictionaries. Each dictionary corresponds to a record
        for which all dependencies are met.

    """

    dir_path = os.path.dirname(os.path.realpath(__file__))
    json_path = os.path.join(dir_path, "dependents.json")

    with open(json_path) as f:
        data = json.load(f)

    instruments_to_process = []

    for record in grouped_records:
        dependencies = data[record["instrument"]][record["level"]]
        result = query_dependents(cur, version_number, [record["date"]], dependencies)

        # Check if all dependencies for this date are present in result
        all_dependencies_met = all_dependency_present(result, dependencies)

        if all_dependencies_met:
            print(f"All dependencies for {record['date']} are met!")
            instruments_to_process.append(
                {
                    "instrument": record["instrument"],
                    "level": record["level"],
                    "date": record["date"],
                }
            )
        else:
            print(f"Some dependencies for {record['date']} are missing!")

    return instruments_to_process


def all_dependency_present(result, dependencies):
    """
    Checks if all specified dependencies are present
    in the given result.

    Parameters
    ----------
    result : list of dict
        Result of a query.
    dependencies : list of dict
        List of required dependencies.

    Returns
    -------
    bool
        True if all dependencies are found in
        the `result`, otherwise False.

    """
    result_list = [{"instrument": r["instrument"], "level": r["level"]} for r in result]

    # Convert dependencies to lowercase for comparison
    dependencies = [
        {"instrument": d["instrument"].lower(), "level": d["level"]}
        for d in dependencies
    ]

    # Check if the items in dependencies are all present in result_list
    if set(tuple(item.items()) for item in dependencies).issubset(
        set(tuple(item.items()) for item in result_list)
    ):
        print("All dependencies are found.")
        return True
    else:
        print("Some dependencies are missing.")
        return False


def lambda_handler(event: dict, context):
    """Handler function"""
    logger.info(f"Event: {event}")
    logger.info(f"Context: {context}")

    instrument = os.environ.get("INSTRUMENT")
    instrument_dependents = os.environ.get("INSTRUMENT_DEPENDENTS")
    state_machine_arn = os.environ.get("STATE_MACHINE_ARN")

    filename = get_filename_from_event(event)

    with db_connect() as conn:
        with conn.cursor() as cur:
            level, version, process_dates = get_process_details(
                cur, instrument, filename
            )
            all_records = query_dependents(
                cur, version, process_dates, instrument_dependents[level]
            )
            grouped_records = remove_ingested(
                all_records, instrument_dependents[level], process_dates
            )
            instruments_to_process = query_dependencies(cur, grouped_records, version)

    # Start Step function execution
    input_data = {
        "instruments_to_process": instruments_to_process,
        "command": f"{instrument}",
    }
    response = step_function_client.start_execution(
        stateMachineArn=state_machine_arn,
        input=json.dumps(input_data),
    )
    logger.info(f"Step function execution started: {response}")
