"""Main file to store schema definition"""
from sqlalchemy import Boolean, Column, DateTime, Integer, String
from sqlalchemy.orm import DeclarativeBase


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


class MetadataTable:
    """Common Metadata table"""

    # TODO: determine cap for strings
    id = Column(Integer, primary_key=True)
    file_path = Column(String, nullable=False)
    instrument = Column(String(6), nullable=False)
    data_level = Column(String(3), nullable=False)
    descriptor = Column(String, nullable=False)
    start_date = Column(DateTime(timezone=True), nullable=False)
    end_date = Column(DateTime(timezone=True), nullable=False)
    ingestion_date = Column(DateTime(timezone=True), nullable=False)
    version = Column(Integer, nullable=False)
    extension = Column(String, nullable=False)


# TODO: Follow-up PR should add in columns for each instrument
# for instrument dependency IDs, SPICE ID, parent id,
# and pointing id


class LoTable(MetadataTable, Base):
    """IMAP-Lo Metadata Table"""

    __tablename__ = "lo"


class HiTable(MetadataTable, Base):
    """IMAP-Hi Metadata Table"""

    __tablename__ = "hi"


class UltraTable(MetadataTable, Base):
    """IMAP-Ultra Metadata Table"""

    __tablename__ = "ultra"


class HITTable(MetadataTable, Base):
    """HIT Metadata Table"""

    __tablename__ = "hit"


class IDEXTable(MetadataTable, Base):
    """IDEX Metadata Table"""

    __tablename__ = "idex"


class SWAPITable(MetadataTable, Base):
    """SWAPI Metadata Table"""

    __tablename__ = "swapi"


class SWETable(MetadataTable, Base):
    """SWE Metadata Table"""

    __tablename__ = "swe"


class CoDICETable(MetadataTable, Base):
    """CoDICE Metadata Table"""

    __tablename__ = "codice"


class MAGTable(MetadataTable, Base):
    """MAG Metadata Table"""

    __tablename__ = "mag"


class GLOWSTable(MetadataTable, Base):
    """GLOWS MetadataTable"""

    __tablename__ = "glows"
