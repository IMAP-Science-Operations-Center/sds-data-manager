from dagster import Definitions
from orchestration import glows, idex, custom_partitions, spin, pointing_attitude, spice


defs = Definitions(
    assets=glows.assets + \
           idex.assets,
    sensors=glows.sensors + \
            idex.sensors + \
            spin.sensors + \
            pointing_attitude.sensors + \
            custom_partitions.sensors + \
            spice.sensors
)