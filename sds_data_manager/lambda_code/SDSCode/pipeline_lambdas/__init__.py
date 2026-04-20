import datetime

VALID_CADENCE_STRS = ["1mo", "3mo", "6mo", "1yr"]

REPOINT_DEPENDENT_INSTRUMENTS = ["glows", "hi", "lo", "ultra"]

NON_DAILY_INSTRUMENTS = ["idex"]

FIRST_MAP_START_DATE = datetime.datetime(2026, 1, 17, tzinfo=datetime.timezone.utc)

LAUNCH_DATE = datetime.datetime(2025, 9, 24, tzinfo=datetime.timezone.utc)
