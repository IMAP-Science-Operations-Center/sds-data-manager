import datetime

VALID_CADENCE_STRS = ["1mo", "3mo", "6mo", "1yr"]

REPOINT_DEPENDENT_INSTRUMENTS = ["glows", "hi", "lo", "ultra"]

NON_DAILY_INSTRUMENTS = ["idex"]

FIRST_MAP_START_DATE = datetime.datetime(2026, 1, 17, tzinfo=datetime.timezone.utc)

LAUNCH_DATE = datetime.datetime(2025, 9, 24, tzinfo=datetime.timezone.utc)

L3_CRON_JOBS = [
    (
        "glows",
        "l3b",
        "ion-rate-profile",
    ),
    (
        "lo",
        "l3",
        "all-maps",
    ),
    (
        "hi",
        "l3",
        "sp-maps",
    ),
    (
        "hi",
        "l3",
        "hic-maps",
    ),
    (
        "ultra",
        "l3",
        "u45-maps",
    ),
    (
        "ultra",
        "l3",
        "u90-maps",
    ),
    (
        "ultra",
        "l3",
        "ulc-sp-maps",
    ),
    (
        "ultra",
        "l3",
        "ulc-nsp-maps",
    ),
]
