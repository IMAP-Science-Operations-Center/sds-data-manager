"""Submit the Data Release 2 manifests to the IMAP SDC.

Usage:
    IMAP_API_KEY=$prod_key python release_manifests.py

Each manifest is uploaded and released independently. A failure (for example
a 409 because that manifest filename was already uploaded) is reported and the
script moves on to the next file. Releases cannot be undone.
"""

import sys
from pathlib import Path

import imap_data_access
from imap_data_access.io import IMAPDataAccessError, release

MANIFEST_DIR = Path(__file__).parent

MANIFESTS = [
    "imap_codice_data-release-002_20260201_20260731_v003.txt",
    "imap_glows_data-release-002_20250924_20260731_v003.txt",
    "imap_hit_data-release-002_20260201_20260518_v001.txt",
    "imap_hit_data-release-002_20260519_20260731_v001.txt",
    "imap_idex_data-release-002_20260201_20260731_v002.txt",
    "imap_mag_data-release-002_20251101_20260731_v002.txt",
    "imap_swapi_data-release-002_20250924_20260731_v002.txt",
    "imap_swe_data-release-002_20250924_20260731_v002.txt",
]


def main():
    """Confirm, then release each manifest and print a summary."""
    if not imap_data_access.config["API_KEY"]:
        sys.exit("IMAP_API_KEY is not set.")

    paths = [MANIFEST_DIR / name for name in MANIFESTS]
    missing = [path.name for path in paths if not path.exists()]
    if missing:
        sys.exit(f"Missing manifest files: {missing}")

    print(f"Target API: {imap_data_access.config['DATA_ACCESS_URL']}")
    print("Manifests to release:")
    for path in paths:
        print(f"  {path.name}")
    if input("Type 'release' to continue: ").strip() != "release":
        sys.exit("Aborted, nothing was released.")

    failures = {}
    for path in paths:
        print(f"Releasing {path.name} ...", flush=True)
        try:
            release(release_type="release", manifest_file=path)
        except (IMAPDataAccessError, ValueError) as e:
            failures[path.name] = str(e)
            print(f"  FAILED: {e}")
        else:
            print("  OK")

    print(f"\n{len(paths) - len(failures)}/{len(paths)} manifests released.")
    for name, error in failures.items():
        print(f"  FAILED {name}: {error}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
