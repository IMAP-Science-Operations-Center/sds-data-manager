"""Stores the IMAP SDC database schema definition.

This module is used to define the database Object Relational Mappers (ORMs).
Each class within maps to a table in the database.
"""

from enum import Enum

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Identity,
    Index,
    Integer,
    String,
    UniqueConstraint,
    and_,
)
from sqlalchemy import (
    Enum as SqlEnum,
)
from sqlalchemy.orm import DeclarativeBase

# Instrument name Enums for the file catalog table
INSTRUMENTS = SqlEnum(
    "codice",
    "glows",
    "hi",
    "hit",
    "idex",
    "lo",
    "mag",
    "swapi",
    "swe",
    "ultra",
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

# 'hard' dependency means that the dependent instrument's processing cannot
# proceed without the primary instrument's data. 'soft' dependency means that
# the dependent instrument's processing can proceed without the primary
# instrument's data. It's nice to have but not necessary.
DEPENDENCY_RELATIONSHIPS = SqlEnum("SOFT", "HARD", name="dependency_relationship")


class Status(Enum):
    """Enum to store the status."""

    INPROGRESS = "INPROGRESS"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


STATUSES = SqlEnum(Status)


class Base(DeclarativeBase):
    """Base class."""

    pass


class UniversalSpinTable(Base):
    """Universal Spin Table schema."""

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


class ProcessingJob(Base):
    """Track all processing jobs."""

    __tablename__ = "processing_job_table"

    id = Column(Integer, Identity(start=1, increment=1), primary_key=True)
    status = Column(STATUSES, nullable=False)
    instrument = Column(INSTRUMENTS, nullable=False)
    data_level = Column(DATA_LEVELS, nullable=False)
    descriptor = Column(String, nullable=False)
    start_date = Column(DateTime, nullable=False)
    version = Column(String(8), nullable=False)
    # TODO:
    # Didn't make it required field yet. Revisit this
    # post discussion
    job_definition = Column(String)
    job_log_stream_id = Column(String)
    container_image = Column(String)
    container_command = Column(String)
    processing_time = Column(Integer)

    __table_args__ = (
        # Partial unique index to ensure only one INPROGRESS or COMPLETED for a record
        # We do want to allow multiple FAILED records
        # NOTE: This does not work with sqllite (testing) DBs, only postgres
        Index(
            "idx_unique_status",
            "instrument",
            "data_level",
            "descriptor",
            "start_date",
            "version",
            unique=True,
            postgresql_where=and_(status.in_(["INPROGRESS", "SUCCEEDED"])),
        ),
    )


class FileCatalog(Base):
    """File catalog table."""

    __tablename__ = "file_catalog"
    __table_args__ = (
        UniqueConstraint(
            "id",
            "file_path",
            "instrument",
            "data_level",
            "start_date",
            name="file_catalog_uc",
        ),
    )

    # TODO: determine cap for strings
    id = Column(Integer, Identity(start=1, increment=1), primary_key=True)
    file_path = Column(String, nullable=False)
    instrument = Column(INSTRUMENTS, nullable=False)
    data_level = Column(DATA_LEVELS, nullable=False)
    # TODO: determine character limit for descriptor
    descriptor = Column(String, nullable=False)
    start_date = Column(DateTime, nullable=False)
    repointing = Column(Integer, nullable=True)
    version = Column(String(4), nullable=False)  # vXXX
    extension = Column(EXTENSIONS, nullable=False)
    ingestion_date = Column(DateTime(timezone=True))


class Version(Base):
    """Version table."""

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
    # TODO: determine cap for strings based on what software version
    # will look like
    software_version = Column(String(2), nullable=False)
    # Data version is a string of the form vXXX
    data_version = Column(String(4), nullable=False)
    updated_date = Column(DateTime, nullable=False)


# TODO: Create table for SPICE file tracking
