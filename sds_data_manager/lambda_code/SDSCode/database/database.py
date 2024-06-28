"""Create database URI that will be used to create engine or make query."""

import json
import os
from contextlib import contextmanager

import boto3
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def get_engine():
    """Create engine from DB URI.

    Returns
    -------
        sqlalchemy.engine.Engine : Engine

    """
    secret_name = os.getenv("SECRET_NAME")
    session = boto3.session.Session()
    client = session.client(service_name="secretsmanager")
    secret_string = client.get_secret_value(SecretId=secret_name)["SecretString"]
    db_config = json.loads(secret_string)
    db_uri = f'postgresql://{db_config["username"]}:{db_config["password"]}@{db_config["host"]}:{db_config["port"]}/{db_config["dbname"]}'

    return create_engine(db_uri)


@contextmanager
class Session:
    """Create session from engine.

    Setting it up this way allows us to more easily mock this behavior in tests.
    """

    def __call__(self):
        """Make a session from the engine."""
        yield sessionmaker(get_engine())()
