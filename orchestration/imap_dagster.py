from dagster import Definitions
from orchestration import glows, idex, custom_partitions, spin, repoint_file, spice


defs = Definitions(
    assets=glows.assets + \
           idex.assets,
    sensors=glows.sensors + \
            idex.sensors + \
            spin.sensors + \
            repoint_file.sensors + \
            custom_partitions.sensors + \
            spice.sensors
)