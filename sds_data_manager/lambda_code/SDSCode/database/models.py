"""Stores the IMAP SDC database schema definition

This module is used to define the database Object Relational Mappers (ORMs).
Each class within maps to a table in the database.
"""

from enum import Enum

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SqlEnum,
)
from sqlalchemy.orm import DeclarativeBase

# Instrument name Enums for the file catalog table
INSTRUMENTS = SqlEnum(
    "codice",
    "glows",
    "hi-45",
    "hi-90",
    "hit",
    "idex",
    "lo",
    "mag",
    "swapi",
    "swe",
    "ultra-45",
    "ultra-90",
    name="instrument",
)

# data level enums for the file catalog table
DATA_LEVELS = SqlEnum(
    "l0",
    "l1",
    "l1a",
    "l1b",
    "l1c",
    "l1ca",
    "l1cb",
    "l1d",
    "l2",
    "l2pre",
    "l3",
    "l3a",
    "l3b",
    "l3c",
    "l3d",
    name="data_level",
)

# extension enums for the file catalog table
EXTENSIONS = SqlEnum("pkts", "cdf", name="extensions")

# "upstream" dependency means an instrument's processing depends on the existence
# of another instrument's data
# "downstream" dependency means that the instrument's data is used in another
# instrument's processing
DEPENDENCY_DIRECTIONS = SqlEnum("UPSTREAM", "DOWNSTREAM", name="dependency_direction")


class Status(Enum):
    INPROGRESS = "INPROGRESS"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


STATUSES = SqlEnum(Status)


class Base(DeclarativeBase):
    pass


class UniversalSpinTable(Base):
    """Universal Spin Table schema"""

    __tablename__ = "universal_spin_table"
    id = Column(Integer, primary_key=True)
    spin_number = Column(Integer, nullable=False)
    spin_start_sc_time = Column(Integer, nullable=False)
    spin_start_utc_time = Column(DateTime, nullable=False)
    star_tracker_flag = Column(Boolean, nullable=False)
    spin_duration = Column(Integer, nullable=False)
    thruster_firing_event = Column(Boolean, nullable=False)
    repointing = Column(Boolean, nullable=False)
    # TODO: create table for repointing and then make
    # a foreign key to universal_spin_table
    repointing_number = Column(Integer, nullable=False)


class StatusTracking(Base):
    """Status tracking table"""

    __tablename__ = "status_tracking"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "file_path_to_create",
            "status",
            name="status_tracking_uc",
        ),
    )

    id = Column(Integer, Identity(start=1, increment=1), primary_key=True)
    file_path_to_create = Column(String, nullable=False)
    status = Column(STATUSES, nullable=False)
    job_definition = Column(String)
    ingestion_date = Column(DateTime)


class FileCatalog(Base):
    """File catalog table"""

    __tablename__ = "file_catalog"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "file_path",
            "instrument",
            "data_level",
            "start_date",
            "end_date",
            name="file_catalog_uc",
        ),
    )

    # TODO: determine cap for strings
    id = Column(Integer, Identity(start=1, increment=1), primary_key=True)
    file_path = Column(String, nullable=False)
    instrument = Column(INSTRUMENTS, nullable=False)
    data_level = Column(DATA_LEVELS, nullable=False)
    descriptor = Column(String(20), nullable=False)
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime, nullable=False)
    version = Column(String(8), nullable=False)
    extension = Column(EXTENSIONS, nullable=False)
    status_tracking_id = Column(
        Integer, ForeignKey("status_tracking.id"), nullable=False
    )


class PreProcessingDependency(Base):
    """Preprocessing dependency table"""

    __tablename__ = "preprocessing_dependency"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "primary_instrument",
            "primary_data_level",
            "primary_descriptor",
            "dependent_instrument",
            "dependent_data_level",
            "dependent_descriptor",
            "relationship",
            "direction",
            name="preprocessing_dependency_uc",
        ),
    )

    # TODO: improve this table after February demo
    id = Column(Integer, Identity(start=1, increment=1), primary_key=True)
    primary_instrument = Column(INSTRUMENTS, nullable=False)
    primary_data_level = Column(DATA_LEVELS, nullable=False)
    primary_descriptor = Column(String, nullable=False)
    dependent_instrument = Column(INSTRUMENTS, nullable=False)
    dependent_data_level = Column(DATA_LEVELS, nullable=False)
    dependent_descriptor = Column(String, nullable=False)
    relationship = Column(String, nullable=False)
    direction = Column(DEPENDENCY_DIRECTIONS, nullable=False)


class Version(Base):
    """Version table"""

    __tablename__ = "version"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "instrument",
            "data_level",
            "software_version",
            "data_version",
            "updated_date",
            name="version_uc",
        ),
    )

    # TODO: improve this table after February demo
    id = Column(Integer, Identity(start=1, increment=1), primary_key=True)
    instrument = Column(INSTRUMENTS, nullable=False)
    data_level = Column(DATA_LEVELS, nullable=False)
    software_version = Column(String(2), nullable=False)
    data_version = Column(String(2), nullable=False)
    updated_date = Column(DateTime, nullable=False)


# TODO: Create table for SPICE file tracking
