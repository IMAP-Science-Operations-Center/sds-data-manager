import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta

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


def db_connect(db_secret_arn):
    """
    Retrieves secrets and connects to database.

    Parameters
    ----------
    db_secret_arn : str
        The ARN for the database secrets in AWS Secrets Manager.

    Returns
    -------
    conn : psycopg.Connection
        Database connection.
    """
    client = boto3.client(service_name="secretsmanager", region_name="us-west-2")

    try:
        response = client.get_secret_value(SecretId=db_secret_arn)
        secret_string = response["SecretString"]
        secret = json.loads(secret_string)
    except Exception as e:
        raise Exception(f"Error retrieving secret: {e}") from e

    try:
        conn = psycopg2.connect(
            dbname=secret["dbname"],
            user=secret["username"],
            password=secret["password"],
            host=secret["host"],
            port=secret["port"],
        )
    except Exception as e:
        raise Exception(f"Error connecting to the database: {e}") from e

    return conn


def get_process_details(cur, instrument, filename, process_range=1):
    """
    Gets details for instrument listed in event.

    Parameters
    ----------
    cur : psycopg.extensions.cursor
        A psycopg database cursor object to execute
        database operations.
    instrument : str
        The name of the instrument for which details
        are to be retrieved.
    filename : str
        The filename associated with the instrument.
    process_range : int
        Numbers of days backwards to process
        e.g. 1 means process all data from 1 day ago

    Returns
    -------
    level : str
        Instrument level
    version : int
        Version
    process_dates : list
        Dates to process
    """

    query = f"""SELECT * FROM sdc.{instrument.lower()}
                WHERE filename = %s;"""
    params = (filename,)

    cur.execute(query, params)
    column_names = [desc[0] for desc in cur.description]
    records = cur.fetchall()

    if not records:
        raise ValueError(f"No records found for filename: {filename}")

    # Check if more than one record is found
    if len(records) > 1:
        raise ValueError(
            f"Expected a single record for filename: {filename}, "
            f"but found multiple records."
        )

    record_dict = dict(zip(column_names, records[0]))

    level = record_dict["level"]
    version = record_dict["version"]

    date = record_dict["date"]
    date_start = date - timedelta(days=process_range)

    # Generate all the dates between date_start and date, inclusive
    current_date = date_start
    process_dates = []

    while current_date <= date:
        process_dates.append(current_date.strftime("%Y-%m-%d"))
        current_date += timedelta(days=1)

    return level, version, process_dates


def query_instruments(cur, version, process_dates, instruments):
    """
    Queries the database for instruments and retrieves their records.

    Parameters
    ----------
    cur : psycopg.extensions.cursor
        A psycopg database cursor object to execute database operations.
    version : int
        Version of the instrument to be queried.
    process_dates : list
        A list containing start and end date to filter records on their ingestion date.
    instruments : list of dict
        A list containing dictionaries of instruments and their levels.
        Each dictionary should have keys 'instrument' and 'level'.

    Returns
    -------
    all_records : list of dict
        A list of dictionaries where each dictionary corresponds to a record
        from the database that matches the given criteria.

    """
    all_records = []

    # Loop through instruments and query them
    for instrument in instruments:
        query = f"""SELECT * FROM sdc.{instrument['instrument'].lower()}
                    WHERE version = %s
                    AND level = %s
                    AND ingested BETWEEN %s::DATE AND (
                    %s::DATE + INTERVAL '1 DAY');"""
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


def remove_ingested(records, inst_list, process_dates):
    """
    Identifies and returns a list of instruments
    that have not been ingested for the specified dates.

    Parameters
    ----------
    records : list of dict
        A list of dictionaries where each dictionary
        corresponds to a record from the database.
    inst_list : list of dict
        A list containing dictionaries of
        instruments and their levels.
    process_dates : list
        A list of date strings representing the dates
        to be checked for missing ingestions.

    Returns
    -------
    output : list of dict
        A list of dictionaries where each dictionary
        indicates an instrument and its level
        that has not been ingested for a given date.

    """

    output = []

    records_set = {
        (rec["date"].date(), rec["instrument"], rec["level"]) for rec in records
    }

    # Here we are removing the ingested instruments from the
    # list of the instruments that we want to ingest.
    for inst in inst_list:
        for date_str in process_dates:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()

            if (
                date_obj,
                inst["instrument"].lower(),
                inst["level"].lower(),
            ) not in records_set:
                output.append(
                    {
                        "instrument": inst["instrument"],
                        "level": inst["level"],
                        "date": date_str,
                    }
                )

    return output


def query_upstream_dependencies(cur, uningested, version):
    """
    Queries and checks if the records are needed for their downstream dependencies.

    Parameters
    ----------
    cur : database cursor
        The cursor object associated with the database connection.
    uningested : list of dict
        A list of dictionaries where each dictionary corresponds to a record
        from the database with keys 'instrument', 'level', and 'date'.
    version : int or str
        The version number to be used when querying records.

    Returns
    -------
    instruments_to_process : list of dict
        A list of dictionaries. Each dictionary corresponds to a record
        that can be processed as its downstream dependencies are unmet.

    """

    dir_path = os.path.dirname(os.path.realpath(__file__))
    json_path = os.path.join(dir_path, "downstream_dependents.json")

    with open(json_path) as f:
        data = json.load(f)

    instruments_to_process = []

    for record in uningested:
        upstream_dependencies = []

        # Iterate over each key-value pair in the data dictionary
        for instr, levels in data.items():
            # Iterate over each level and its corresponding dependencies
            for level, deps in levels.items():
                # Check if there's any dependency that matches the criteria
                dependency_found = False
                for dep in deps:
                    if (
                        dep["instrument"] == record["instrument"]
                        and dep["level"] == record["level"]
                    ):
                        dependency_found = True
                        break  # Found a matching dependency

                # If a matching dependency was found, add a dictionary to the list
                if dependency_found:
                    upstream_dependencies.append({"instrument": instr, "level": level})

        # Check if all dependencies for this date are present in result
        result = query_instruments(
            cur, version, [record["date"]], upstream_dependencies
        )
        all_dependencies_met = all_dependency_present(result, upstream_dependencies)

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


def prepare_data(instruments_to_process):
    """
    Groups input data by 'instrument' and 'level', and aggregates the dates
    for each group into a list.

    Parameters
    ----------
    instruments_to_process : list of dict
        A list of dictionaries which is not aggregated.

    Returns
    -------
    grouped_dict: dict
        A dictionary of instruments, each containing a dictionary of
        levels with a list of dates.
    """
    grouped_data = defaultdict(lambda: defaultdict(list))
    for item in instruments_to_process:
        instrument = item["instrument"]
        level = item["level"]
        date = item["date"]
        grouped_data[instrument][level].append(date)

    grouped_dict = {
        instrument: dict(levels) for instrument, levels in grouped_data.items()
    }

    return grouped_dict


def lambda_handler(event: dict, context):
    """Handler function"""
    logger.info(f"Event: {event}")
    logger.info(f"Context: {context}")

    instrument = os.environ.get("INSTRUMENT")
    instrument_downstream = os.environ.get("INSTRUMENT_DOWNSTREAM")
    state_machine_arn = os.environ.get("STATE_MACHINE_ARN")
    db_secret_arn = os.environ.get("SECRET_ARN")

    filename = get_filename_from_event(event)

    with db_connect(db_secret_arn) as conn:
        with conn.cursor() as cur:
            # get details of the object
            level, version, process_dates = get_process_details(
                cur, instrument, filename
            )
            # query downstream dependents to see if they have been ingested
            # e.g. if codice_l0_20230401_v01 object created the event, has
            # codice_l1a_20230401_v01 been ingested already? This is to
            # check for duplicates, but maybe we should just handle duplicates
            # in batch job and remove this.
            # TODO: this can only be for daily data products (not ENA or GLOWS); remove?
            ingested_dependents = query_instruments(
                cur, version, process_dates, instrument_downstream[level]
            )
            # TODO: this can only be for daily data products (not ENA or GLOWS); remove?
            # remove downstream dependents that have been ingested
            uningested = remove_ingested(
                ingested_dependents, instrument_downstream[level], process_dates
            )

            # TODO: this can only be for daily data products (not ENA or GLOWS); remove?
            # Check if uningested is empty
            if not uningested:
                logger.info("No uningested downstream dependents found.")
                return

            # TODO: add universal spin table query for ENAs and GLOWS
            # decide if we have sufficient upstream dependencies
            downstream_instruments_to_process = query_upstream_dependencies(
                cur, uningested, version
            )

            # No instruments to process
            if not downstream_instruments_to_process:
                logger.info("No instruments_to_process. Skipping further processing.")
                return

        grouped_list = prepare_data(downstream_instruments_to_process)

        # Start Step function execution for each instrument
        for instrument_name in grouped_list:
            input_data = {
                "command": [
                    instrument_name,
                    f"{grouped_list[instrument_name]}",
                    f"{version}",
                ]
            }

            response = step_function_client.start_execution(
                stateMachineArn=state_machine_arn,
                name=f"{instrument_name}",
                input=json.dumps(input_data),
            )
            print(
                f"Started Step Function for {instrument_name} with response: {response}"
            )
