# Files copied into the dev data bucket at the start of a run. Copying a file
# under an ``imap/<instrument>/`` prefix triggers the indexer lambda and, in
# turn, Dagster processing. Add more (source_bucket, key) tuples over time.

SOURCE_FILES = [
    ### REPOINT FILE
    ( # Covers first year of mission
        "sds-data-593025701104",
        "imap/spice/repoint/imap_2026_191_01.repoint",
    ),

    ### SPICE FILES
    ( # Leapseconds
        "sds-data-593025701104",
        "imap/spice/lsk/naif0012.tls",
    ),
    ( # Planetary Constants Kernel
        "sds-data-593025701104",
        "imap/spice/pck/pck00011.tpc",
    ),
    ( # Spacecraft Clock Kernel
        "sds-data-593025701104",
        "imap/spice/sclk/imap_sclk_0225.tsc",
    ),
    ( # Frame Kernels
        "sds-data-593025701104",
        "imap/spice/fk/imap_130.tf",
    ),
    ( # Science Frame Kernels
        "sds-data-593025701104",
        "imap/spice/fk/imap_science_120.tf",
    ),
    ( # Planetary Ephemeris
        "sds-data-593025701104",
        "imap/spice/spk/de440.bsp",
    ),
    ( # Reconstructed Ephemeris
        "sds-data-593025701104",
        "imap/spice/spk/imap_recon_20250925_20260601_v01.bsp"
    ),
    ( # Attitude History
        "sds-data-593025701104",
        "imap/spice/ck/imap_2025_358_2026_085_004.ah.bc"
    ),

    ### SPIN FILES
    (
        "sds-data-593025701104",
        "imap/spice/spin/imap_2025_365_2026_001_01.spin",
    ),
    (
        "sds-data-593025701104",
        "imap/spice/spin/imap_2026_001_2026_002_01.spin",
    ),
    (
        "sds-data-593025701104",
        "imap/spice/spin/imap_2026_002_2026_003_01.spin",
    ),
    
    ### GLOWS
    ( # Level 0
        "sds-data-593025701104",
        "imap/glows/l0/2026/01/imap_glows_l0_raw_20260101-repoint00096_v001.0002.pkts",
    ),
    ( # Ancillary
        "sds-data-593025701104",
        "imap/ancillary/glows/imap_glows_pipeline-settings_20251112_v002.json",
    ),
    ( # Ancillary
        "sds-data-593025701104",
        "imap/ancillary/glows/imap_glows_l1b-exclusions-by-instr-team_20251112_v003.dat",
    ),
    ( # Ancillary
        "sds-data-593025701104",
        "imap/ancillary/glows/imap_glows_l1b-map-of-excluded-regions_20251112_v001.dat",
    ),
    ( # Ancillary
        "sds-data-593025701104",
        "imap/ancillary/glows/imap_glows_l1b-map-of-uv-sources_20250923_v001.dat",
    ),
    ( # Ancillary
        "sds-data-593025701104",
        "imap/ancillary/glows/imap_glows_l1b-suspected-transients_20251112_v002.dat",
    ),
    ( # Ancillary
        "sds-data-593025701104",
        "imap/ancillary/glows/imap_glows_l1b-conversion-table-for-anc-data_20251112_v001.json",
    ),
    ( # Ancillary
        "sds-data-593025701104",
        "imap/ancillary/glows/imap_glows_l2-calibration_20251112_v004.dat",
    ),
    ( # Ancillary
        "sds-data-593025701104",
        "imap/ancillary/glows/imap_glows_l3a-time-dep-bckgrd_20251112_v001.dat",
    ),
    ( # Ancillary
        "sds-data-593025701104",
        "imap/ancillary/glows/imap_glows_l3a-map-of-extra-helio-bckgrd_20251112_v001.dat",
    ),

]