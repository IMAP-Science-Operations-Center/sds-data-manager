"""Stores the downstream and upstream dependency configuration of some IMAP instruments.

This is used to populate pre-processing dependency table in the database.

NOTE: This setup assumes that we get one data file with multiple APIDs data.
This is why we have only one dependency for l0. We expect that we get one
l0 file, eg. imap_idex_l0_raw_20240529_v001.pkts, which contains all the data of all
APIDs. That l0 data file will kick off one l1a process for 'all' as l1a will produce
multiple files with different descriptor(aka different data product per APID). Those
different descriptor are handled by CDF attrs.
"""

from ..database.models import PreProcessingDependency

downstream_dependents = [
    PreProcessingDependency(
        primary_instrument="idex",
        primary_data_level="l0",
        primary_descriptor="raw",
        dependent_instrument="idex",
        dependent_data_level="l1",
        dependent_descriptor="sci",
        relationship="HARD",
        direction="DOWNSTREAM",
    ),
    PreProcessingDependency(
        primary_instrument="swapi",
        primary_data_level="l0",
        primary_descriptor="raw",
        dependent_instrument="swapi",
        dependent_data_level="l1",
        dependent_descriptor="all",
        relationship="HARD",
        direction="DOWNSTREAM",
    ),
    PreProcessingDependency(
        primary_instrument="swe",
        primary_data_level="l0",
        primary_descriptor="raw",
        dependent_instrument="swe",
        dependent_data_level="l1a",
        dependent_descriptor="sci",
        relationship="HARD",
        direction="DOWNSTREAM",
    ),
    PreProcessingDependency(
        primary_instrument="swe",
        primary_data_level="l1a",
        primary_descriptor="sci",
        dependent_instrument="swe",
        dependent_data_level="l1b",
        dependent_descriptor="sci",
        relationship="HARD",
        direction="DOWNSTREAM",
    ),
    PreProcessingDependency(
        primary_instrument="hit",
        primary_data_level="l1a",
        primary_descriptor="sci",
        dependent_instrument="hit",
        dependent_data_level="l1b",
        dependent_descriptor="sci",
        relationship="HARD",
        direction="DOWNSTREAM",
    ),
]

# UPSTREAM DEPENDENCIES
upstream_dependents = [
    PreProcessingDependency(
        primary_instrument="idex",
        primary_data_level="l1",
        primary_descriptor="sci",
        dependent_instrument="idex",
        dependent_data_level="l0",
        dependent_descriptor="raw",
        relationship="HARD",
        direction="UPSTREAM",
    ),
    PreProcessingDependency(
        primary_instrument="swapi",
        primary_data_level="l1",
        primary_descriptor="all",
        dependent_instrument="swapi",
        dependent_data_level="l0",
        dependent_descriptor="raw",
        relationship="HARD",
        direction="UPSTREAM",
    ),
    PreProcessingDependency(
        primary_instrument="swe",
        primary_data_level="l1a",
        primary_descriptor="sci",
        dependent_instrument="swe",
        dependent_data_level="l0",
        dependent_descriptor="raw",
        relationship="HARD",
        direction="UPSTREAM",
    ),
    PreProcessingDependency(
        primary_instrument="swe",
        primary_data_level="l1b",
        primary_descriptor="sci",
        dependent_instrument="swe",
        dependent_data_level="l1a",
        dependent_descriptor="sci",
        relationship="HARD",
        direction="UPSTREAM",
    ),
    PreProcessingDependency(
        primary_instrument="hit",
        primary_data_level="l1a",
        primary_descriptor="sci",
        dependent_instrument="hit",
        dependent_data_level="l0",
        dependent_descriptor="sci",
        relationship="HARD",
        direction="UPSTREAM",
    ),
    PreProcessingDependency(
        primary_instrument="hit",
        primary_data_level="l1b",
        primary_descriptor="sci",
        dependent_instrument="hit",
        dependent_data_level="l1a",
        dependent_descriptor="sci",
        relationship="HARD",
        direction="UPSTREAM",
    ),
    PreProcessingDependency(
        primary_instrument="hit",
        primary_data_level="l3",
        primary_descriptor="sci",
        dependent_instrument="hit",
        dependent_data_level="l2",
        dependent_descriptor="sci",
        relationship="HARD",
        direction="UPSTREAM",
    ),
]
