r"""Copy released files from the data bucket to the public data bucket.

The public data bucket is a second, direct way to make released IMAP data
public, independent of the SPDF archive. This script queries the database for
every released file and copies each one that isn't in the public bucket yet,
keeping the same S3 key. Files already in the public bucket are never
overwritten or deleted, so the script is safe to re-run.

This is a temporary, manual solution. It will be replaced once the team either
makes this copy the nominal workflow, run automatically whenever data is
released, or picks another way to publish released files, such as AWS Open
Data or an on-prem SFTP server (to avoid S3 egress costs).

By default only science files are copied, since the release process only
controls those. Ancillary files are released on ingestion; pass
``--table ancillary`` (alone or alongside ``--table science``) to copy those too.

Every released version is copied, not just the latest, because older released
versions stay public through the API as well.

Run it from a machine that can reach the production database, with AWS
credentials for the production account::

    SECRET_NAME=sdp-database-cred \
        poetry run python -m scripts.release.copy_released_files_to_public_bucket \
        --dry-run

``--dry-run`` lists the files that would be copied without copying them. Drop it
to copy. The script exits with status 1 if any copy fails.
"""

import argparse
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

import boto3
from sqlalchemy import select

from sds_data_manager.lambda_code.SDSCode.database import database as db
from sds_data_manager.lambda_code.SDSCode.database import models

logger = logging.getLogger(__name__)

SOURCE_BUCKET = "sds-data-593025701104"
DEST_BUCKET = "sds-public-data-593025701104"

# Tables whose released files can be copied
TABLES = {
    "science": models.ScienceFiles,
    "ancillary": models.AncillaryFiles,
}


@dataclass
class CopySummary:
    """Results of copying released files to the public bucket."""

    released: int = 0
    already_public: int = 0
    copied: list[str] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)


def get_released_file_paths(table_names: list[str]) -> list[str]:
    """Get the S3 keys of every released file in the given tables.

    Parameters
    ----------
    table_names : list[str]
        Keys of ``TABLES`` to query.

    Returns
    -------
    list[str]
        The released files' S3 keys, sorted.
    """
    file_paths = set()
    with db.Session() as session:
        for table_name in table_names:
            table = TABLES[table_name]
            file_paths.update(
                session.scalars(select(table.file_path).where(table.released))
            )
    return sorted(file_paths)


def list_bucket_keys(s3_client, bucket: str) -> set[str]:
    """List every object key in a bucket."""
    paginator = s3_client.get_paginator("list_objects_v2")
    return {
        obj["Key"]
        for page in paginator.paginate(Bucket=bucket)
        for obj in page.get("Contents", [])
    }


def copy_released_files(
    table_names: list[str],
    source_bucket: str = SOURCE_BUCKET,
    dest_bucket: str = DEST_BUCKET,
    dry_run: bool = False,
    workers: int = 16,
) -> CopySummary:
    """Copy released files that aren't in the public bucket yet.

    Parameters
    ----------
    table_names : list[str]
        Keys of ``TABLES`` to copy released files from.
    source_bucket : str
        Bucket the files are copied from.
    dest_bucket : str
        Public bucket the files are copied to.
    dry_run : bool
        If True, log the files that would be copied without copying them.
    workers : int
        Number of copies to run at once.

    Returns
    -------
    CopySummary
        Counts, the files copied, and per-file failures.
    """
    s3_client = boto3.client("s3")
    released = get_released_file_paths(table_names)
    public = list_bucket_keys(s3_client, dest_bucket)
    to_copy = [file_path for file_path in released if file_path not in public]

    summary = CopySummary(
        released=len(released), already_public=len(released) - len(to_copy)
    )
    if dry_run:
        for file_path in to_copy:
            logger.info(f"Would copy {file_path}")
        summary.copied = to_copy
        return summary

    def _copy(file_path):
        # Managed copy, so files over 5 GB are copied in parts
        s3_client.copy(
            CopySource={"Bucket": source_bucket, "Key": file_path},
            Bucket=dest_bucket,
            Key=file_path,
        )

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_copy, file_path): file_path for file_path in to_copy
        }
        for future in as_completed(futures):
            file_path = futures[future]
            try:
                future.result()
                logger.info(f"Copied {file_path}")
                summary.copied.append(file_path)
            except Exception as e:
                logger.exception(f"Failed to copy {file_path}")
                summary.failures[file_path] = str(e)

    summary.copied.sort()
    return summary


def main(argv: list[str] | None = None) -> int:
    """Run the copy from the command line.

    Parameters
    ----------
    argv : list[str], optional
        Command-line arguments. Defaults to sys.argv.

    Returns
    -------
    int
        Exit status: 0 on success, 1 if any copy failed.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--table",
        choices=sorted(TABLES),
        action="append",
        dest="tables",
        help="Table to copy released files from. Repeat for several. Default: science.",
    )
    parser.add_argument("--source-bucket", default=SOURCE_BUCKET)
    parser.add_argument("--dest-bucket", default=DEST_BUCKET)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List the files that would be copied without copying them.",
    )
    parser.add_argument(
        "--workers", type=int, default=16, help="Number of copies to run at once."
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    tables = args.tables or ["science"]
    summary = copy_released_files(
        tables,
        source_bucket=args.source_bucket,
        dest_bucket=args.dest_bucket,
        dry_run=args.dry_run,
        workers=args.workers,
    )

    action = "would copy" if args.dry_run else "copied"
    print(
        f"{summary.released} released files in {', '.join(tables)}: "
        f"{summary.already_public} already in {args.dest_bucket}, "
        f"{action} {len(summary.copied)}, {len(summary.failures)} failed"
    )
    for file_path, error in summary.failures.items():
        print(f"  FAILED {file_path}: {error}")
    return 1 if summary.failures else 0


if __name__ == "__main__":
    sys.exit(main())
