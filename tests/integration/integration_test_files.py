"""Files copied into the dev data bucket at the start of a run.

Copying a file under an ``imap/<instrument>/`` prefix triggers
the indexer lambda and, in turn, Dagster processing.

Add more (source_bucket, key) tuples over time.
"""

PROD_BUCKET = "sds-data-593025701104"

PROD_SOURCE_FILE_PATHS = [
    ### REPOINT FILE
    # Covers first year of mission
    "imap/spice/repoint/imap_2026_191_01.repoint",
    ### SPICE FILES
    # Leapseconds
    "imap/spice/lsk/naif0012.tls",
    # Planetary Constants Kernel
    "imap/spice/pck/pck00011.tpc",
    # Spacecraft Clock Kernel
    "imap/spice/sclk/imap_sclk_0225.tsc",
    # Frame Kernels
    "imap/spice/fk/imap_130.tf",
    # Science Frame Kernels
    "imap/spice/fk/imap_science_120.tf",
    # Planetary Ephemeris
    "imap/spice/spk/de440.bsp",
    # Reconstructed Ephemeris
    "imap/spice/spk/imap_recon_20250925_20260601_v01.bsp",
    # Attitude History
    "imap/spice/ck/imap_2025_358_2026_085_004.ah.bc",
    ### SPIN FILES
    "imap/spice/spin/imap_2025_365_2026_001_01.spin",
    "imap/spice/spin/imap_2026_001_2026_002_01.spin",
    "imap/spice/spin/imap_2026_002_2026_003_01.spin",
    ### GLOWS
    # Level 0
    "imap/glows/l0/2026/01/imap_glows_l0_raw_20260101-repoint00096_v001.0002.pkts",
    # Ancillary
    "imap/ancillary/glows/imap_glows_pipeline-settings_20251112_v002.json",
    "imap/ancillary/glows/imap_glows_l1b-exclusions-by-instr-team_20251112_v003.dat",
    "imap/ancillary/glows/imap_glows_l1b-map-of-excluded-regions_20251112_v001.dat",
    "imap/ancillary/glows/imap_glows_l1b-map-of-uv-sources_20250923_v001.dat",
    "imap/ancillary/glows/imap_glows_l1b-suspected-transients_20251112_v002.dat",
    "imap/ancillary/glows/imap_glows_l1b-conversion-table-for-anc-data_20251112_v001.json",
    "imap/ancillary/glows/imap_glows_l2-calibration_20251112_v004.dat",
    "imap/ancillary/glows/imap_glows_l3a-time-dep-bckgrd_20251112_v001.dat",
    "imap/ancillary/glows/imap_glows_l3a-map-of-extra-helio-bckgrd_20251112_v001.dat",
]

TEST_FILES = [(PROD_BUCKET, fpath) for fpath in PROD_SOURCE_FILE_PATHS]
