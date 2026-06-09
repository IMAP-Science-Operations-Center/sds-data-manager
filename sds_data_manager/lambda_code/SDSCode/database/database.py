"""Create database URI that will be used to create engine or make query."""

import json
import os
from contextlib import contextmanager

import boto3
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

_ENGINE = None


def get_engine():
    """Create engine from DB URI.

    Returns
    -------
        sqlalchemy.engine.Engine : Engine

    """
    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE

    secret_name = os.getenv("SECRET_NAME")
    session = boto3.session.Session()
    client = session.client(service_name="secretsmanager")
    secret_string = client.get_secret_value(SecretId=secret_name)["SecretString"]
    db_config = json.loads(secret_string)
    db_uri = f"postgresql://{db_config['username']}:{db_config['password']}@{db_config['host']}:{db_config['port']}/{db_config['dbname']}"

    _ENGINE = create_engine(db_uri, poolclass=NullPool)

    return create_engine(db_uri)


@contextmanager
def Session():  # noqa: N802
    """Create session from engine."""
    # This now pulls from the shared connection pool!
    session = sessionmaker(bind=get_engine())()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()  # Returns the connection to the pool, doesn't destroy it
