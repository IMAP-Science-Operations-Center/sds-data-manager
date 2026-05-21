from dagster import Definitions
from orchestration import glows, custom_partitions, spin, pointing_attitude, spice, imap_assets


defs = Definitions(
    assets=glows.assets + \
            imap_assets.assets,
    sensors=glows.sensors + \
            spin.sensors + \
            pointing_attitude.sensors + \
            custom_partitions.sensors + \
            spice.sensors + \
            imap_assets.sensors
)