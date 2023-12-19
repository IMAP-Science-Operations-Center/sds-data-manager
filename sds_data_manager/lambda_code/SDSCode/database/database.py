"""Create engine that will be used to create schema or make query"""
from SDSCode.database import config
from sqlalchemy import create_engine

engine = create_engine(config.db_uri, echo=True)
