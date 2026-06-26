"""Lambda to transition S3 files to new version."""

import logging
import os
import tempfile
from pathlib import Path

import boto3
import imap_data_access
from imap_data_access.file_validation import (
    ScienceFilePath,
    Version,
    generate_imap_file_path,
)
from imap_processing.cdf.utils import load_cdf, write_cdf

from . import database as db
from . import models

# Copy files from the old path to the new path?
COPY_FILES: bool = True
# Destination prefix for copied files
DEST_PREFIX: str = "renamed/"
# Update the file_path column in the database?
# Do this once all files are moved from `DEST_PREFIX` to the canonical path on S3
# bucket (after making a backup of the canonical path on the S3 bucket, of course).
MODIFY_ROWS: bool = False

# Maximum number of CDFs to process; None for all (for testing on dev)
MAX_CDFS: int | None = 3
# Reverse the sense of `old` vs `new` paths? (for testing on dev)
REVERSE: bool = True

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)


def upload_cdf(client, bucket: str, src_key: str, dst_key: str, dst_version: str):
    """Rewrite a CDF to add the new version attribute."""
    with tempfile.NamedTemporaryFile(suffix=".cdf") as tmp:
        client.download_fileobj(bucket, src_key, tmp)
        tmp.flush()
        dataset = load_cdf(tmp.name)

    # From @tech3371 - `Data_version` is sans `v`
    dataset.attrs["Data_version"] = dst_version.lstrip("v")

    # Making guarantees about spdf conformance on existing files is out of scope
    written = Path(write_cdf(dataset, istp=True, terminate_on_warning=False))
    try:
        client.upload_file(str(written), bucket, dst_key)
    finally:
        written.unlink(missing_ok=True)
    logger.info(f"Wrote CDF {src_key} -> s3://{bucket}/{dst_key}")


def get_s3_keys(bucket, prefix="imap/"):
    """Return the set of all object keys in ``bucket`` under ``prefix``."""
    client = boto3.client("s3")
    paginator = client.get_paginator("list_objects_v2")
    keys = set()
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        keys.update(obj["Key"] for obj in page.get("Contents", []))
    return keys


def lambda_handler(event, context):  # noqa: PLR0912, PLR0915
    """Lambda handler for transitioning S3 files to new version.

    This lambda is used one-time to transition s3 files to new
    version. This lambda is used in dev and prod to test
    and apply the transition of s3 files in production.

    Lambda code defined here will help with making sure
    dev and prod has same setup when transitioning.
    """
    assert not all([COPY_FILES, MODIFY_ROWS]), "Please do this in stages!"

    data_dir = imap_data_access.config["DATA_DIR"]

    bucket = os.getenv("S3_BUCKET")
    if not bucket:
        raise ValueError("S3_BUCKET environment variable is not set")
    logger.info(f"Listing objects in s3://{bucket}/imap/ ...")
    s3_keys = get_s3_keys(bucket)
    logger.info(f"Found {len(s3_keys)} objects in the bucket")

    n_cdfs = 0

    with db.Session() as session:
        count = session.query(models.ScienceFiles).count()
        logger.info(f"Verifying file_path mapping for {count} records")

        # (current_path, current_version_str) => (new_path, new_version_str)
        path_mapping: dict[tuple[str, str], tuple[str, str]] = {}

        for row in session.query(models.ScienceFiles):
            # Construct the new filename from scratch using the table columns. Use the
            # class of the existing path so the directory prefix (science, dependency,
            # etc.) is preserved.
            imap_file = generate_imap_file_path(row.file_path)
            file_cls = type(imap_file)
            if not issubclass(file_cls, ScienceFilePath):
                raise ValueError(f"Unexpected file type: {file_cls}")

            old_version = f"v{row.minor_version:03}"  # no help from Version() here
            old_file = file_cls.generate_from_inputs(
                instrument=row.instrument,
                data_level=row.data_level,
                descriptor=row.descriptor,
                start_time=row.start_date.strftime("%Y%m%d"),
                version=old_version,
                extension=row.extension,
                repointing=row.repointing,
                cr=row.cr,
            )

            new_version = str(Version(row.major_version, row.minor_version))
            # identical to old_file, but with new version
            new_file = file_cls.generate_from_inputs(
                instrument=row.instrument,
                data_level=row.data_level,
                descriptor=row.descriptor,
                start_time=row.start_date.strftime("%Y%m%d"),
                version=new_version,
                extension=row.extension,
                repointing=row.repointing,
                cr=row.cr,
            )

            # construct_path() prepends DATA_DIR; strip it
            old_file_path = str(old_file.construct_path().relative_to(data_dir))
            new_file_path = str(new_file.construct_path().relative_to(data_dir))

            # Sanity test - we should match naive str replacement
            naive_file_path = old_file_path.replace(
                f"_{old_version}.", f"_{new_version}.", 1
            )
            if naive_file_path != new_file_path:
                raise ValueError(
                    f"Mismatch for {old_file_path}: "
                    f"naive={naive_file_path!r} vs from_scratch={new_file_path!r}"
                )

            if new_file_path.endswith(".cdf"):
                n_cdfs += 1
                if MAX_CDFS is None or n_cdfs <= MAX_CDFS:
                    path_mapping[(old_file_path, old_version)] = (
                        new_file_path,
                        new_version,
                    )

        if REVERSE:
            rename_map = {v: k for k, v in path_mapping.items()}
        else:
            rename_map = dict(path_mapping)

        for (src_path, _), (dst_path, _) in rename_map.items():
            logger.info(f"Mapping {src_path} -> {dst_path}")

        dst_paths = list(rename_map.values())
        assert len(set(dst_paths)) == len(dst_paths), "Duplicates in dst_paths!"

        if COPY_FILES:
            client = boto3.client("s3")
            for (src_path, _), (dst_path, dst_version) in rename_map.items():
                if src_path == dst_path:
                    continue
                if src_path not in s3_keys:
                    raise ValueError(f"Cannot read missing object: {src_path}")

                dst_key = f"{DEST_PREFIX}{dst_path}"
                if dst_path.endswith(".cdf"):
                    upload_cdf(client, bucket, src_path, dst_key, dst_version)
                else:
                    client.copy_object(
                        Bucket=bucket,
                        CopySource={"Bucket": bucket, "Key": src_path},
                        Key=dst_key,
                    )
                    logger.info(f"Copied {src_path} -> {dst_key}")
            logger.info("All destination files written")

        # Updating rows does not use `dst_key` at all. It is assumed that after making
        # a backup of the `imap/` path in the S3 bucket, files will be moved from
        # DEST_PREFIX to the original path in bulk, and then this block will be run.
        if MODIFY_ROWS:
            for src_path, dst_path in rename_map.items():
                session.query(models.ScienceFiles).filter(
                    models.ScienceFiles.file_path == src_path
                ).update(
                    {models.ScienceFiles.file_path: dst_path},
                    synchronize_session=False,
                )
            session.commit()
            logger.info(f"Updated file_path for {len(rename_map)} records")

    return {
        "statusCode": 200,
        "body": "S3 file transition completed successfully.",
    }
