"""Main file to store schema definition"""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Integer,
    String,
)
from sqlalchemy.orm import DeclarativeBase

# Instrument name Enums for the file catalog table
instruments = Enum(
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
data_levels = Enum(
    "l0",
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

# status enums for the status tracking table
statuses = Enum("INPROGRESS", "SUCCEEDED", "FAILED", name="status")


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

    id = Column(Integer, Identity(start=1, increment=1), primary_key=True)
    file_to_create_path = Column(String, nullable=False)
    status = Column(statuses, nullable=False)
    job_definition = Column(String, nullable=False)
    ingestion_date = Column(DateTime, nullable=True)


class FileCatalog(Base):
    """File catalog table"""

    __tablename__ = "file_catalog"

    # TODO: determine cap for strings
    id = Column(Integer, Identity(start=1, increment=1), primary_key=True)
    file_path = Column(String, nullable=False)
    instrument = Column(instruments, nullable=False)
    data_level = Column(data_levels, nullable=False)
    descriptor = Column(String, nullable=False)
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime, nullable=False)
    version = Column(String, nullable=False)
    extension = Column(String, nullable=False)
    status_tracking_id = Column(Integer, ForeignKey("status_tracking.id"))
