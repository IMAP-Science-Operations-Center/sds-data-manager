r"""Re-index existing spin and small forces files into the SPICE table.

Spin and small forces (SFF) files used to be indexed only into their own tables
(spin_files and small_forces_files), with dates taken from their filenames. This
backfills spice_files with each file's real coverage, read from its contents.

Each row keeps the ingestion_date of its spin_files / small_forces_files row, so
sensors that track ingestion dates don't see the backfilled rows as new files. The
date is copied rather than read from the S3 object's last modified time, which
changes if the object is ever rewritten (e.g. copied or restored). Afterwards the
script reports any row whose dates still don't match. Rows are upserted on file
name, so the script is safe to re-run, and a re-run repairs any wrong dates.

Run it from a machine that can reach the environment's database, with AWS
credentials for that environment's account::

    SECRET_NAME=sdp-database-cred S3_BUCKET=sds-data-<account id> \
        poetry run python -m scripts.spice.reindex_spin_and_sff_files --dry-run

``--dry-run`` reads each file's coverage without writing to the database. Drop it
to write. Use ``--type spin`` or ``--type thruster`` to backfill only one file
type. The script exits with status 1 if any file fails, or if afterwards any file
in the spin_files / small_forces_files table is missing from spice_files or has a
different ingestion_date there.
"""

import argparse
import logging
import os
import sys
from dataclasses import dataclass, field

from sqlalchemy import select

from sds_data_manager.lambda_code.SDSCode.database import database as db
from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas import spice_indexer
from sds_data_manager.lambda_code.SDSCode.spice_utilities import download_from_s3

logger = logging.getLogger(__name__)

# The table each file type was indexed into before the SPICE table
LEGACY_TABLES = {
    "spin": models.SpinFiles,
    "thruster": models.SmallForcesFile,
}


@dataclass
class ReindexSummary:
    """Results of re-indexing one file type."""

    kernel_type: str
    total: int = 0
    processed: int = 0
    failures: dict[str, str] = field(default_factory=dict)
    # Files in the legacy table that have no row in the SPICE table
    missing: list[str] = field(default_factory=list)
    # Files whose SPICE table ingestion_date differs from their legacy row's
    mismatched_dates: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Whether every file was re-indexed with its legacy ingestion date."""
        return not (self.failures or self.missing or self.mismatched_dates)


def _read_coverage(file_path: str, kernel_type: str):
    """Download a file and read its coverage, without writing to the database."""
    local_path = download_from_s3(file_path)
    try:
        return spice_indexer.COVERAGE_FROM_FILE_CONTENTS[kernel_type](local_path)
    finally:
        spice_indexer.clear_ephemeral_storage(local_path)


def compare_with_legacy_table(kernel_type: str) -> tuple[list[str], list[str]]:
    """Compare the legacy table's files with their rows in the SPICE table.

    Parameters
    ----------
    kernel_type : str
        The file type, "spin" or "thruster".

    Returns
    -------
    missing : list[str]
        Files with no row in the SPICE table, sorted.
    mismatched_dates : list[str]
        Files whose SPICE table ingestion_date differs from their legacy row's,
        sorted.
    """
    legacy = LEGACY_TABLES[kernel_type]
    spice = models.SPICEFiles
    with db.Session() as session:
        legacy_dates = dict(
            session.execute(select(legacy.file_path, legacy.ingestion_date)).all()
        )
        spice_dates = dict(
            session.execute(
                select(spice.file_path, spice.ingestion_date).where(
                    spice.kernel_type == kernel_type
                )
            ).all()
        )
    missing = sorted(set(legacy_dates) - set(spice_dates))
    mismatched_dates = sorted(
        file_path
        for file_path, ingestion_date in legacy_dates.items()
        if file_path in spice_dates and spice_dates[file_path] != ingestion_date
    )
    return missing, mismatched_dates


def reindex_files(kernel_type: str, dry_run: bool = False) -> ReindexSummary:
    """Re-index every file of one type from its legacy table into the SPICE table.

    Parameters
    ----------
    kernel_type : str
        The file type, "spin" or "thruster".
    dry_run : bool
        If True, read each file's coverage but don't write to the database.

    Returns
    -------
    ReindexSummary
        Counts, per-file failures, and files missing from the SPICE table.
    """
    legacy = LEGACY_TABLES[kernel_type]
    with db.Session() as session:
        rows = session.execute(
            select(legacy.file_path, legacy.ingestion_date).order_by(legacy.file_path)
        ).all()

    summary = ReindexSummary(kernel_type, total=len(rows))
    for file_path, ingestion_date in rows:
        try:
            if dry_run:
                start, end = _read_coverage(file_path, kernel_type)
                logger.info(f"Would index {file_path}: {start} to {end}")
            else:
                # Keep the legacy row's ingestion date rather than reading the
                # S3 object's last modified time, which changes if the object is
                # ever rewritten. If the legacy row has none, the indexer falls
                # back to the S3 time, which is what the legacy indexer recorded.
                spice_indexer.index_spice_file(file_path, ingestion_date=ingestion_date)
            summary.processed += 1
        except Exception as e:
            logger.exception(f"Failed to re-index {file_path}")
            summary.failures[file_path] = str(e)

    summary.missing, summary.mismatched_dates = compare_with_legacy_table(kernel_type)
    return summary


def _print_summary(summary: ReindexSummary, dry_run: bool):
    """Print the results of re-indexing one file type."""
    action = "read" if dry_run else "re-indexed"
    print(
        f"{summary.kernel_type}: {action} {summary.processed} of {summary.total} "
        f"files, {len(summary.failures)} failed"
    )
    for file_path, error in summary.failures.items():
        print(f"  FAILED {file_path}: {error}")
    print(
        f"{summary.kernel_type}: {len(summary.missing)} of {summary.total} files "
        "have no row in spice_files"
    )
    for file_path in summary.missing:
        print(f"  MISSING {file_path}")
    print(
        f"{summary.kernel_type}: {len(summary.mismatched_dates)} files have a "
        "different ingestion_date in spice_files"
    )
    for file_path in summary.mismatched_dates:
        print(f"  DATE MISMATCH {file_path}")


def main(argv: list[str] | None = None) -> int:
    """Run the backfill from the command line.

    Parameters
    ----------
    argv : list[str], optional
        Command-line arguments. Defaults to sys.argv.

    Returns
    -------
    int
        Exit status: 0 on success, 1 if any file failed or is missing.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--type",
        choices=sorted(LEGACY_TABLES),
        action="append",
        dest="kernel_types",
        help="File type to re-index. Repeat for both. Defaults to both.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read each file's coverage without writing to the database.",
    )
    args = parser.parse_args(argv)

    missing_env = [name for name in ("SECRET_NAME", "S3_BUCKET") if not os.getenv(name)]
    if missing_env:
        parser.error(f"Set these environment variables: {', '.join(missing_env)}")

    logging.basicConfig(level=logging.INFO)
    results = []
    for kernel_type in args.kernel_types or sorted(LEGACY_TABLES):
        summary = reindex_files(kernel_type, dry_run=args.dry_run)
        _print_summary(summary, args.dry_run)
        # In a dry run, files still missing from spice_files are expected
        results.append(not summary.failures if args.dry_run else summary.ok)
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
