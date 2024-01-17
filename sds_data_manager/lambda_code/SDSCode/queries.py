# Standard
import itertools
import json
import logging
import sys

from SDSCode.database import models
from SDSCode.database.database import engine
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)


def lambda_handler(event, context):
    """Handler function for making queries.

    Parameters
    ----------
    event : dict
        The JSON formatted document with the data required for the
        lambda function to process
    context : LambdaContext
        This object provides methods and properties that provide
        information about the invocation, function,
        and runtime environment.
    """
    logger.info(f"Event: {event}")
    logger.info(f"Context: {context}")

    logger.info("Received event: " + json.dumps(event, indent=2))

    # The model lookup is used to match the instrument data
    # to the correct postgres table based on the instrument name.
    model_lookup = {
        "lo": models.LoTable,
        "hi": models.HiTable,
        "ultra": models.UltraTable,
        "hit": models.HITTable,
        "idex": models.IDEXTable,
        "swapi": models.SWAPITable,
        "swe": models.SWETable,
        "codice": models.CoDICETable,
        "mag": models.MAGTable,
        "glows": models.GLOWSTable,
    }

    # add session, pick model like in indexer and add query to filter_as
    query_params = event["queryStringParameters"]

    with Session(engine) as session:
        if "instrument" in query_params.keys():
            model = model_lookup[query_params.pop("instrument")]
            query = select(model.__table__)
            for param, value in query_params.items():
                query = query.where(getattr(model, param) == value)
        else:
            all_models = [model_name.__table__ for model_name in model_lookup.values()]
            print(f"ALL MODELS: {all_models}")
            query = select(*all_models)
            print(f"QUERY {query}")
            for model, param in itertools.product(
                model_lookup.values(), query_params.keys()
            ):
                print(f"MODEL: {model}")
                print(f"PARAM: {param}")
                query = query.where(or_(getattr(model, param) == query_params[param]))

        search_result = session.execute(query).all()

    logger.info("Query Search Results: " + str(search_result))

    # Format the response
    response = {
        "statusCode": 200,
        "body": str(search_result),  # Convert JSON data to a string
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",  # Allow CORS
        },
    }
    return response
