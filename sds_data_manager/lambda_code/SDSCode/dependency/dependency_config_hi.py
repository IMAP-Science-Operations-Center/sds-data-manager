"""Stores the downstream and upstream dependency configuration of IMAP-Hi.

This is used to populate pre-processing dependency table in the database.

NOTE: This setup assumes that we get one data file with multiple APIDs data.
This is why we have only one dependency for l0. We expect that we get one
l0 file, eg. imap_hi_l0_raw_20240529_v001.pkts, which contains all the data of all
APIDs. That l0 data file will kick off one l1a process for 'all' as l1a will produce
multiple files with different descriptor(aka different data product per APID). Those
different descriptor are handled by CDF attrs.
"""

from ..database.models import PreProcessingDependency

downstream_dependents = [
    PreProcessingDependency(
        primary_instrument="hi",
        primary_data_level="l0",
        primary_descriptor="raw",
        dependent_instrument="hi",
        dependent_data_level="l1a",
        dependent_descriptor="all",
        relationship="HARD",
        direction="DOWNSTREAM",
    ),
    PreProcessingDependency(
        primary_instrument="hi",
        primary_data_level="l1a",
        primary_descriptor="45-hist",
        dependent_instrument="hi",
        dependent_data_level="l1b",
        dependent_descriptor="45-hist",
        relationship="HARD",
        direction="DOWNSTREAM",
    ),
    PreProcessingDependency(
        primary_instrument="hi",
        primary_data_level="l1a",
        primary_descriptor="45-de",
        dependent_instrument="hi",
        dependent_data_level="l1b",
        dependent_descriptor="45-de",
        relationship="HARD",
        direction="DOWNSTREAM",
    ),
    PreProcessingDependency(
        primary_instrument="hi",
        primary_data_level="l1a",
        primary_descriptor="45-hk",
        dependent_instrument="hi",
        dependent_data_level="l1b",
        dependent_descriptor="45-hk",
        relationship="HARD",
        direction="DOWNSTREAM",
    ),
    PreProcessingDependency(
        primary_instrument="hi",
        primary_data_level="l1b",
        primary_descriptor="45-de",
        dependent_instrument="hi",
        dependent_data_level="l1c",
        dependent_descriptor="45-pset",
        relationship="HARD",
        direction="DOWNSTREAM",
    ),
    # TODO: add 90 sensor data products
]

upstream_dependents = [
    PreProcessingDependency(
        primary_instrument="hi",
        primary_data_level="l1a",
        primary_descriptor="all",
        dependent_instrument="hi",
        dependent_data_level="l0",
        dependent_descriptor="raw",
        relationship="HARD",
        direction="UPSTREAM",
    ),
    PreProcessingDependency(
        primary_instrument="hi",
        primary_data_level="l1b",
        primary_descriptor="45-hist",
        dependent_instrument="hi",
        dependent_data_level="l1a",
        dependent_descriptor="45-hist",
        relationship="HARD",
        direction="UPSTREAM",
    ),
    PreProcessingDependency(
        primary_instrument="hi",
        primary_data_level="l1b",
        primary_descriptor="45-de",
        dependent_instrument="hi",
        dependent_data_level="l1a",
        dependent_descriptor="45-de",
        relationship="HARD",
        direction="UPSTREAM",
    ),
    PreProcessingDependency(
        primary_instrument="hi",
        primary_data_level="l1b",
        primary_descriptor="45-hk",
        dependent_instrument="hi",
        dependent_data_level="l1a",
        dependent_descriptor="45-hk",
        relationship="HARD",
        direction="UPSTREAM",
    ),
    PreProcessingDependency(
        primary_instrument="hi",
        primary_data_level="l1c",
        primary_descriptor="45-pset",
        dependent_instrument="hi",
        dependent_data_level="l1b",
        dependent_descriptor="45-de",
        relationship="HARD",
        direction="UPSTREAM",
    ),
    # TODO: add 90 sensor data products
]
