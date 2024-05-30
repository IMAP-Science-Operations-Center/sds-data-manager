"""Stores the downstream dependency configuration of Ultra.

This is used to populate pre-processing dependency table in the database.

NOTE: This setup assumes that we get one data file everyday with all
the data of multiple apid. This is why we have only one file for each
l0 and descriptor is raw. And l1a that depends on l0 has 'all' as
descriptor with assumption that l1a could produce multiple files
with different descriptor. Those different descriptor are handle in its own
code in imap_processing repo.
"""

from .database.models import PreProcessingDependency

downstream_dependents = [
    PreProcessingDependency(
        primary_instrument="ultra",
        primary_data_level="l0",
        primary_descriptor="raw",
        dependent_instrument="ultra",
        dependent_data_level="l1a",
        dependent_descriptor="all",
        relationship="HARD",
        direction="DOWNSTREAM",
    ),
    # Ultra-45
    PreProcessingDependency(
        primary_instrument="ultra",
        primary_data_level="l1a",
        primary_descriptor="45de",
        dependent_instrument="ultra",
        dependent_data_level="l1b",
        dependent_descriptor="45all",
        relationship="HARD",
        direction="DOWNSTREAM",
    ),
    PreProcessingDependency(
        primary_instrument="ultra",
        primary_data_level="l1a",
        primary_descriptor="45phxtof",
        dependent_instrument="ultra",
        dependent_data_level="l1b",
        dependent_descriptor="45all",
        relationship="HARD",
        direction="DOWNSTREAM",
    ),
    PreProcessingDependency(
        primary_instrument="ultra",
        primary_data_level="l1a",
        primary_descriptor="45aux",
        dependent_instrument="ultra",
        dependent_data_level="l1b",
        dependent_descriptor="45all",
        relationship="HARD",
        direction="DOWNSTREAM",
    ),
    PreProcessingDependency(
        primary_instrument="ultra",
        primary_data_level="l1a",
        primary_descriptor="45rates",
        dependent_instrument="ultra",
        dependent_data_level="l1b",
        dependent_descriptor="45all",
        relationship="HARD",
        direction="DOWNSTREAM",
    ),
    # TODO: uncomment for post-SIT-3
    # PreProcessingDependency(
    #     primary_instrument="codice",
    #     primary_data_level="l2",
    #     primary_descriptor="todo",
    #     dependent_instrument="ultra",
    #     dependent_data_level="l1b",
    #     dependent_descriptor="45all",
    #     relationship="HARD",
    #     direction="DOWNSTREAM",
    # ),
    # PreProcessingDependency(
    #     primary_instrument="swapi",
    #     primary_data_level="l2",
    #     primary_descriptor="todo",
    #     dependent_instrument="ultra",
    #     dependent_data_level="l1b",
    #     dependent_descriptor="45all",
    #     relationship="HARD",
    #     direction="DOWNSTREAM",
    # ),
    PreProcessingDependency(
        primary_instrument="ultra",
        primary_data_level="l1b",
        primary_descriptor="45annotated-de",
        dependent_instrument="ultra",
        dependent_data_level="l1c",
        dependent_descriptor="45all",
        relationship="HARD",
        direction="DOWNSTREAM",
    ),
    PreProcessingDependency(
        primary_instrument="ultra",
        primary_data_level="l1b",
        primary_descriptor="45extended-spin",
        dependent_instrument="ultra",
        dependent_data_level="l1c",
        dependent_descriptor="45all",
        relationship="HARD",
        direction="DOWNSTREAM",
    ),
    PreProcessingDependency(
        primary_instrument="ultra",
        primary_data_level="l1b",
        primary_descriptor="45culling-mask",
        dependent_instrument="ultra",
        dependent_data_level="l1c",
        dependent_descriptor="45all",
        relationship="HARD",
        direction="DOWNSTREAM",
    ),
    PreProcessingDependency(
        primary_instrument="ultra",
        primary_data_level="l1b",
        primary_descriptor="45badtimes",
        dependent_instrument="ultra",
        dependent_data_level="l1c",
        dependent_descriptor="45all",
        relationship="HARD",
        direction="DOWNSTREAM",
    ),
    # Ultra-90
    PreProcessingDependency(
        primary_instrument="ultra",
        primary_data_level="l1a",
        primary_descriptor="90de",
        dependent_instrument="ultra",
        dependent_data_level="l1b",
        dependent_descriptor="90all",
        relationship="HARD",
        direction="DOWNSTREAM",
    ),
    PreProcessingDependency(
        primary_instrument="ultra",
        primary_data_level="l1a",
        primary_descriptor="90phxtof",
        dependent_instrument="ultra",
        dependent_data_level="l1b",
        dependent_descriptor="90all",
        relationship="HARD",
        direction="DOWNSTREAM",
    ),
    PreProcessingDependency(
        primary_instrument="ultra",
        primary_data_level="l1a",
        primary_descriptor="90aux",
        dependent_instrument="ultra",
        dependent_data_level="l1b",
        dependent_descriptor="90all",
        relationship="HARD",
        direction="DOWNSTREAM",
    ),
    PreProcessingDependency(
        primary_instrument="ultra",
        primary_data_level="l1a",
        primary_descriptor="90rates",
        dependent_instrument="ultra",
        dependent_data_level="l1b",
        dependent_descriptor="90all",
        relationship="HARD",
        direction="DOWNSTREAM",
    ),
    # TODO: uncomment for post-SIT-3
    # PreProcessingDependency(
    #     primary_instrument="codice",
    #     primary_data_level="l2",
    #     primary_descriptor="todo",
    #     dependent_instrument="ultra",
    #     dependent_data_level="l1b",
    #     dependent_descriptor="90all",
    #     relationship="HARD",
    #     direction="DOWNSTREAM",
    # ),
    # PreProcessingDependency(
    #     primary_instrument="swapi",
    #     primary_data_level="l2",
    #     primary_descriptor="todo",
    #     dependent_instrument="ultra",
    #     dependent_data_level="l1b",
    #     dependent_descriptor="90all",
    #     relationship="HARD",
    #     direction="DOWNSTREAM",
    # ),
    PreProcessingDependency(
        primary_instrument="ultra",
        primary_data_level="l1b",
        primary_descriptor="90annotated-de",
        dependent_instrument="ultra",
        dependent_data_level="l1c",
        dependent_descriptor="90all",
        relationship="HARD",
        direction="DOWNSTREAM",
    ),
    PreProcessingDependency(
        primary_instrument="ultra",
        primary_data_level="l1b",
        primary_descriptor="90extended-spin",
        dependent_instrument="ultra",
        dependent_data_level="l1c",
        dependent_descriptor="90all",
        relationship="HARD",
        direction="DOWNSTREAM",
    ),
    PreProcessingDependency(
        primary_instrument="ultra",
        primary_data_level="l1b",
        primary_descriptor="90culling-mask",
        dependent_instrument="ultra",
        dependent_data_level="l1c",
        dependent_descriptor="90all",
        relationship="HARD",
        direction="DOWNSTREAM",
    ),
    PreProcessingDependency(
        primary_instrument="ultra",
        primary_data_level="l1b",
        primary_descriptor="90badtimes",
        dependent_instrument="ultra",
        dependent_data_level="l1c",
        dependent_descriptor="90all",
        relationship="HARD",
        direction="DOWNSTREAM",
    ),
]

# UPSTREAM DEPENDENCIES
# This will need to change for inter-instrument dependencies
upstream_dependents = []

for dep in downstream_dependents:
    upstream_dependents.append(dep.reverse_direction())
