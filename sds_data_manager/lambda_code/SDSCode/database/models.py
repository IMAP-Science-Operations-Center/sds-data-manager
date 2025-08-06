"""Stores the IMAP SDC database schema definition.

This module is used to define the database Object Relational Mappers (ORMs).
Each class within maps to a table in the database.
"""

from enum import Enum

import imap_data_access
from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Float,
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

# Instrument name Enums for the ScienceFiles table
INSTRUMENTS = SqlEnum(
    *imap_data_access.VALID_INSTRUMENTS,
    name="instrument",
)

# data level enums for the ScienceFiles table
DATA_LEVELS = SqlEnum(
    *imap_data_access.VALID_DATALEVELS,
    name="data_level",
)

# extension enums for the ScienceFiles table
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
    started_at = Column(DateTime(timezone=True))
    stopped_at = Column(DateTime(timezone=True))

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

    def to_dict(self):
        """Convert the ProcessingJob instance to a dictionary."""
        return {
            "status": str(self.status.name),
            "instrument": str(self.instrument),
            "data_level": str(self.data_level),
            "descriptor": self.descriptor,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "version": self.version,
            # These parameters could be None when the batch job is in progress
            "job_definition": self.job_definition if self.job_definition else None,
            "job_log_stream_id": self.job_log_stream_id
            if self.job_log_stream_id
            else None,
            "container_image": self.container_image if self.container_image else None,
            "container_command": self.container_command
            if self.container_command
            else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "stopped_at": self.stopped_at.isoformat() if self.stopped_at else None,
        }


class ScienceFiles(Base):
    """Science files table."""

    __tablename__ = "science_files"

    file_path = Column(String, nullable=False, primary_key=True, unique=True)
    instrument = Column(INSTRUMENTS, nullable=False)
    data_level = Column(DATA_LEVELS, nullable=False)
    # TODO: determine character limit for descriptor
    descriptor = Column(String, nullable=False)
    start_date = Column(DateTime, nullable=False)
    repointing = Column(Integer, nullable=True)
    version = Column(String(4), nullable=False)  # vXXX
    extension = Column(EXTENSIONS, nullable=False)
    ingestion_date = Column(DateTime(timezone=True))
    cr = Column(Integer, nullable=True)
    crid = Column(String, nullable=True)


class SPICEFiles(Base):
    """SPICE files table."""

    __tablename__ = "spice_files"

    file_path = Column(String, nullable=False, primary_key=True, unique=True)
    file_name = Column(String, nullable=False, unique=True)
    ingestion_date = Column(DateTime(timezone=True))
    file_root = Column(String)
    kernel_type = Column(String)
    min_date_j2000 = Column(Float)
    max_date_j2000 = Column(Float)
    file_intervals_j2000 = Column(JSON)
    min_date_datetime = Column(DateTime(timezone=True))
    max_date_datetime = Column(DateTime(timezone=True))
    file_intervals_datetime = Column(JSON)
    min_date_sclk = Column(String)
    max_date_sclk = Column(String)
    file_intervals_sclk = Column(JSON)
    sclk_kernel = Column(String)
    lsk_kernel = Column(String)
    version = Column(Integer, nullable=True)


class AncillaryFiles(Base):
    """Ancillary files table."""

    __tablename__ = "ancillary_files"

    file_path = Column(String, nullable=False, primary_key=True, unique=True)
    instrument = Column(INSTRUMENTS, nullable=False)
    # TODO: determine character limit for descriptor
    descriptor = Column(String, nullable=False)
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime, nullable=True)
    version = Column(String(4), nullable=False)  # vXXX
    extension = Column(String, nullable=False)
    ingestion_date = Column(DateTime(timezone=True))


class SpinFiles(Base):
    """Spin files table."""

    __tablename__ = "spin_files"
    # Spin number will be unique
    file_path = Column(String, nullable=False, primary_key=True, unique=True)
    # start and end date from file name
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime, nullable=False)
    version = Column(String(2), nullable=False)
    ingestion_date = Column(DateTime(timezone=True))


class PointingTable(Base):
    """Pointing table."""

    __tablename__ = "pointing_table"
    pointing_id = Column(Integer, nullable=False, primary_key=True, unique=True)
    pointing_start_utc = Column(DateTime(timezone=True), nullable=False)
    pointing_end_utc = Column(DateTime(timezone=True))
    repoint_start_utc = Column(DateTime(timezone=True))
    repoint_end_utc = Column(DateTime(timezone=True))


class RepointFiles(Base):
    """Repoint table."""

    __tablename__ = "repoint_files"
    file_path = Column(String, nullable=False, primary_key=True, unique=True)
    end_date = Column(DateTime, nullable=False)
    version = Column(String(2), nullable=False)
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
