"""Main file to store schema definition"""
import enum

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


class InstrumentEnum(enum.Enum):
    """Instrument table enums"""

    CODICE = "codice"
    GLOWS = "glows"
    HI45 = "hi-45"
    HI90 = "hi-90"
    HIT = "hit"
    IDEX = "idex"
    LO = "lo"
    MAG = "mag"
    SWAPI = "swapi"
    SWE = "swe"
    ULTRA45 = "ultra-45"
    ULTRA90 = "ultra-90"


class DataLevelEnum(enum.Enum):
    """Data level enums"""

    L0 = "l0"
    L1A = "l1a"
    L1B = "l1b"
    L1C = "l1c"
    L1CA = "l1ca"
    L1CB = "l1cb"
    L1D = "l1d"
    L2 = "l2"
    L2PRE = "l2pre"
    L3 = "l3"
    L3A = "l3a"
    L3B = "l3b"
    L3C = "l3c"
    L3D = "l3d"


class StatusEnum(enum.Enum):
    """File status enums"""

    SUCCESS = 1
    FAILURE = 0


class Base(DeclarativeBase):
    pass


class UniversalSpinTable(Base):
    """Universal Spin Table schema"""

    __tablename__ = "universal_spin_table"
    id = Column(Integer, primary_key=True)
    spin_number = Column(Integer, nullable=False)
    spin_start_sc_time = Column(Integer, nullable=False)
    spin_start_utc_time = Column(DateTime(timezone=True), nullable=False)
    star_tracker_flag = Column(Boolean, nullable=False)
    spin_duration = Column(Integer, nullable=False)
    thruster_firing_event = Column(Boolean, nullable=False)
    repointing = Column(Boolean, nullable=False)
    # TODO: create table for repointing and then make
    # a foreign key to universal_spin_table
    repointing_number = Column(Integer, nullable=False)


class StatusTrackingTable(Base):
    """Status tracking table"""

    __tablename__ = "status_tracking_table"

    id = Column(Integer, Identity(start=1, increment=1), primary_key=True)
    file_to_create_path = Column(String, nullable=False)
    status = Column(Enum(StatusEnum), nullable=False)
    job_definition = Column(String, nullable=False)
    ingestion_date = Column(DateTime(timezone=True), nullable=True)


class FileCatalogTable(Base):
    """File catalog table"""

    __tablename__ = "file_catalog_table"

    # TODO: determine cap for strings
    id = Column(Integer, Identity(start=1, increment=1), primary_key=True)
    file_path = Column(String, nullable=False)
    instrument = Column(Enum(InstrumentEnum), nullable=False)
    data_level = Column(Enum(DataLevelEnum), nullable=False)
    descriptor = Column(String, nullable=False)
    start_date = Column(DateTime(timezone=True), nullable=False)
    end_date = Column(DateTime(timezone=True), nullable=False)
    version = Column(String, nullable=False)
    extension = Column(String, nullable=False)
    status_tracking_id = Column(Integer, ForeignKey("status_tracking_table.id"))
