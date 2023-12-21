"""Main file to store schema definition"""
from sqlalchemy import Boolean, Column, DateTime, Identity, Integer, String
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


class FileCatalogTable:
    """Common file catalog table"""

    # TODO: determine cap for strings
    id = Column(Integer, Identity(start=1, increment=1), primary_key=True)
    file_path = Column(String, nullable=False)
    instrument = Column(String(6), nullable=False)
    data_level = Column(String(3), nullable=False)
    descriptor = Column(String, nullable=False)
    start_date = Column(DateTime(timezone=True), nullable=False)
    end_date = Column(DateTime(timezone=True), nullable=False)
    ingestion_date = Column(DateTime(timezone=True), nullable=False)
    version = Column(String, nullable=False)
    extension = Column(String, nullable=False)


# TODO: Follow-up PR should add in columns for each instrument
# for instrument dependency IDs, SPICE ID, parent id,
# and pointing id


class LoTable(FileCatalogTable, Base):
    """IMAP-Lo File Catalog Table"""

    __tablename__ = "lo"


class HiTable(FileCatalogTable, Base):
    """IMAP-Hi File Catalog Table"""

    __tablename__ = "hi"


class UltraTable(FileCatalogTable, Base):
    """IMAP-Ultra File Catalog Table"""

    __tablename__ = "ultra"


class HITTable(FileCatalogTable, Base):
    """HIT File Catalog Table"""

    __tablename__ = "hit"


class IDEXTable(FileCatalogTable, Base):
    """IDEX File Catalog Table"""

    __tablename__ = "idex"


class SWAPITable(FileCatalogTable, Base):
    """SWAPI File Catalog Table"""

    __tablename__ = "swapi"


class SWETable(FileCatalogTable, Base):
    """SWE File Catalog Table"""

    __tablename__ = "swe"


class CoDICETable(FileCatalogTable, Base):
    """CoDICE File Catalog Table"""

    __tablename__ = "codice"


class MAGTable(FileCatalogTable, Base):
    """MAG File Catalog Table"""

    __tablename__ = "mag"


class GLOWSTable(FileCatalogTable, Base):
    """GLOWS File Catalog Table"""

    __tablename__ = "glows"
