import json
import logging
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta

import boto3
import psycopg2
from sqlalchemy import func
from sqlalchemy.orm import Session

# Setup the logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create a Step Functions client
batch_client = boto3.client("batch")


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


def query_instruments(db_session, version, process_dates, instruments):
    """
    Queries the database for instruments and retrieves their records.
    ... [rest of your docstring]
    """
    all_records = []

    for instrument in instruments:
        instrument_name = instrument["instrument"].lower()
        instrument_class = globals()[instrument_name.capitalize() + "Table"]

        query = db_session.query(instrument_class).filter(
            instrument_class.version == version,
            instrument_class.data_level == func.lower(instrument["level"]),
            instrument_class.ingestion_date.between(
                min(process_dates), max(process_dates) + timedelta(days=1)
            ),
        )

        records = query.all()
        for record in records:
            # convert the record to a dictionary, or handle as needed
            record_dict = {
                column.name: getattr(record, column.name)
                for column in record.__table__.columns
            }
            all_records.append(record_dict)

    return all_records


def query_upstream_dependencies(cur, downstream_dependents):
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

    # Iterate over each key-value pair
    for instr, levels in data.items():
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


def get_downstream_dependents(instrument, level):
    """
    Retrieves downstream dependents of a given instrument.

    Parameters
    ----------
    key : str
        The key from the JSON file.

    Returns
    -------
    dict
        The value associated with the provided key in the JSON file.
    """

    # Construct the path to the JSON file
    dir_path = os.path.dirname(os.path.realpath(__file__))
    dependency_path = os.path.join(dir_path, "downstream_dependents.json")

    with open(dependency_path) as file:
        data = json.load(file)
        dependents = data[instrument][level]

    return dependents


def extract_components(filename):
    """Extracts components from filename"""
    pattern = (
        r"^imap_"
        r"(?P<instrument>[^_]*)_"
        r"(?P<datalevel>[^_]*)_"
        r"(?P<descriptor>[^_]*)_"
        r"(?P<startdate>\d{8})_"
        r"(?P<enddate>\d{8})_"
        r"(?P<version>v\d{2}-\d{2})"
        r"\.cdf$"
    )
    match = re.match(pattern, filename)
    components = match.groupdict()
    return components


def lambda_handler(event: dict, context):
    """Handler function"""
    logger.info(f"Event: {event}")
    logger.info(f"Context: {context}")

    # Event details:
    filename = event["detail"]["object"]["key"]
    components = extract_components(filename)
    instrument = components["instrument"]
    level = components["datalevel"]

    downstream_dependents = get_downstream_dependents(instrument, level)

    db_secret_arn = os.environ.get("SECRET_ARN")

    session = boto3.session.Session()
    sts_client = boto3.client("sts")
    region = session.region_name
    account = sts_client.get_caller_identity()["Account"]

    job_definition = (
        f"arn:aws:batch:{region}:{account}:job-definition/"
        f"fargate-batch-job-definition{instrument}"
    )
    job_queue = (
        f"arn:aws:batch:{region}:{account}:job-queue/"
        f"{instrument}-fargate-batch-job-queue"
    )

    with Session(database.engine) as session:
        result = query_instruments(
            session,
            1,
            [datetime(2023, 5, 31)],
            [{"instrument": "codicehi", "level": "l1b"}],
        )
        print(result)

    with db_connect(db_secret_arn) as conn:
        with conn.cursor() as cur:
            # TODO: query the version table here for latest version
            #  of each downstream_dependent. Add logic for reprocessing.
            # TODO: add universal spin table query for ENAs and GLOWS
            # decide if we have sufficient upstream dependencies
            downstream_instruments_to_process = query_upstream_dependencies(
                cur, downstream_dependents
            )

            # No instruments to process
            if not downstream_instruments_to_process:
                logger.info("No instruments_to_process. Skipping further processing.")
                return

        grouped_list = prepare_data(downstream_instruments_to_process)

        # TODO: more changes required for dates, version, and descriptor
        # Start Batch Job execution for each instrument
        for instrument_name in grouped_list:
            for data_level in grouped_list[instrument_name]:
                batch_client.submit_job(
                    jobName=f"imap_{instrument_name}_{data_level}_<descriptor>_<startdate>_<enddate>_<vxx-xx>.cdf",
                    jobQueue=job_queue,
                    jobDefinition=job_definition,
                    containerOverrides={
                        "command": [
                            f"imap_{instrument_name}_{data_level}_<descriptor>_<startdate>_<enddate>_<vxx-xx>.cdf"
                        ],
                    },
                )
