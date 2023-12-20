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

    id = Column(Integer, primary_key=True)
    file_name = Column(String, nullable=False)
    # TODO: Do we need instrument since these will be split up into their
    # separate tables?
    instrument = Column(String, nullable=False)
    data_level = Column(String, nullable=False)
    descriptor = Column(String, nullable=False)
    start_date = Column(DateTime(timezone=True), nullable=False)
    end_date = Column(DateTime(timezone=True), nullable=False)
    ingestion_date = Column(DateTime(timezone=True), nullable=False)
    version = Column(Integer, nullable=False)
    format = Column(String, nullable=False)


# TODO: Follow-up PR should add in columns for each instrument
# for instrument dependency IDs


class LoMetadataTable(MetadataTable, Base):
    """IMAP-Lo Metadata Table"""

    __tablename__ = "lo_metadata_table"


class HiMetadataTable(MetadataTable, Base):
    """IMAP-Hi Metadata Table"""

    __tablename__ = "hi_metadata_table"


class UltraMetadataTable(MetadataTable, Base):
    """IMAP-Ultra Metadata Table"""

    __tablename__ = "ultra_metadata_table"


class HITMetadataTable(MetadataTable, Base):
    """HIT Metadata Table"""

    __tablename__ = "hit_metadata_table"


class IDEXMetadataTable(MetadataTable, Base):
    """IDEX Metadata Table"""

    __tablename__ = "idex_metadata_table"


class SWAPIMetadataTable(MetadataTable, Base):
    """SWAPI Metadata Table"""

    __tablename__ = "swapi_metadata_table"


class SWEMetadataTable(MetadataTable, Base):
    """SWE Metadata Table"""

    __tablename__ = "swe_metadata_table"


class CoDICEMetadataTable(MetadataTable, Base):
    """CoDICE Metadata Table"""

    __tablename__ = "codice_metadata_table"


class MAGMetadataTable(MetadataTable, Base):
    """MAG Metadata Table"""

    __tablename__ = "mag_metadata_table"


class GLOWSMetadataTable(MetadataTable, Base):
    """GLOWS MetadataTable"""

    __tablename__ = "glows_metadata_table"
