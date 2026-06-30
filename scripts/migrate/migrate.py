"""Migration script for renaming science files in S3/DB."""

import logging
import os
import tempfile
from pathlib import Path

import boto3
import imap_data_access
from imap_data_access.file_validation import ScienceFilePath, Version
from imap_processing.cdf.utils import load_cdf
from imap_processing.cdf.utils import write_cdf as _write_cdf

from sds_data_manager.lambda_code.SDSCode.database import database as db
from sds_data_manager.lambda_code.SDSCode.database import models

# Destination prefix for copied files
DEST_PREFIX: str = "renamed/"
# Maximum number of CDFs/PKTs to process; None for all (for testing on dev)
MAX_CDFS_PKTS: int | None = None
# Reverse the sense of `old` vs `new` paths? (for testing on dev)
REVERSE: bool = False
# Write a dummy CDF instead of a real one to make the script go fast (for testing)
DUMMY_CDF: bool = False


def write_cdf(dataset, **kwargs):
    """Write a CDF, or a dummy placeholder file if ``DUMMY_CDF`` is set."""
    if DUMMY_CDF:
        with tempfile.NamedTemporaryFile(suffix=".cdf", delete=False) as tmp:
            tmp.write(b"dummy cdf")
            return tmp.name
    return _write_cdf(dataset, **kwargs)


logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
logging.basicConfig(level=logging.INFO)


def remap_parents(dataset, basename_map: dict[str, str]):
    """Update the ``Parents`` attribute to reflect the CDF renaming.

    ``Parents`` is a list of dependency file *basenames* (see imap_processing
    ``cli.py``: ``[p.name for p in dependencies.get_file_paths()]``). Many of
    those parents are themselves science files being renamed by this migration,
    so each basename is remapped via ``basename_map``. Parents not in the map
    (e.g. SPICE/ancillary files) are left unchanged. ``load_cdf`` collapses a
    single-element ``Parents`` to a scalar string.
    """
    parents = dataset.attrs.get("Parents")
    if parents is None:
        return
    if isinstance(parents, str):
        parents = [parents]
    dataset.attrs["Parents"] = [basename_map.get(p, p) for p in list(parents)]


def upload_cdf(
    client,
    bucket: str,
    src_key: str,
    dst_key: str,
    dst_version: str,
    basename_map: dict[str, str],
):
    """Download/modify/upload a pkts/cdf file on S3."""
    if src_key.endswith("pkts"):
        client.copy_object(
            Bucket=bucket,
            CopySource={"Bucket": bucket, "Key": src_key},
            Key=dst_key,
        )
        logger.info(f"Copied PKTS {src_key} -> s3://{bucket}/{dst_key}")
        return

    with tempfile.NamedTemporaryFile(suffix=".cdf") as tmp:
        client.download_fileobj(bucket, src_key, tmp)
        tmp.flush()
        dataset = load_cdf(tmp.name)

    # From @tech3371 - `Data_version` is sans `v`
    dataset.attrs["Data_version"] = dst_version.lstrip("v")

    # Parent filenames embed the old version format and may themselves be
    # renamed science files, so remap them to match the new CDF names.
    remap_parents(dataset, basename_map)

    # Making guarantees about spdf conformance on existing files is out of scope
    written = Path(write_cdf(dataset, istp=True, terminate_on_warning=False))
    try:
        client.upload_file(str(written), bucket, dst_key)
    finally:
        written.unlink(missing_ok=True)
    logger.info(f"Copied CDF {src_key} -> s3://{bucket}/{dst_key}")


def get_s3_keys(bucket, prefix="imap/"):
    """Return the set of all object keys in ``bucket`` under ``prefix``."""
    client = boto3.client("s3")
    paginator = client.get_paginator("list_objects_v2")
    keys = set()
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        keys.update(obj["Key"] for obj in page.get("Contents", []))
    return keys


def migrate(copy_files: bool = False, modify_rows: bool = False):  # noqa: PLR0912, PLR0915
    """Migrate science files in S3 or update the database."""
    assert not all([copy_files, modify_rows]), "Please do this in stages!"

    data_dir = imap_data_access.config["DATA_DIR"]

    bucket = os.getenv("S3_BUCKET")
    if not bucket:
        raise ValueError("S3_BUCKET environment variable is not set")
    logger.info(f"Listing objects in s3://{bucket}/imap/ ...")
    s3_keys = get_s3_keys(bucket)
    logger.info(f"Found {len(s3_keys)} objects in the bucket")

    n_cdfs_pkts = 0

    with db.Session() as session:
        count = session.query(models.ScienceFiles).count()
        logger.info(f"Verifying file_path mapping for {count} records")

        # (current_path, current_version_str) => (new_path, new_version_str)
        path_mapping: dict[tuple[str, str], tuple[str, str]] = {}

        # old_basename => new_basename, covering ALL rows (not just the ones
        # copied this run) so the `Parents` attribute can be fully remapped.
        basename_map: dict[str, str] = {}

        for row in session.query(models.ScienceFiles):
            old_version = str(Version(None, row.minor_version))
            new_version = str(Version(row.major_version, row.minor_version))

            old_suffix = f"_{old_version}.{row.extension}"
            new_suffix = f"_{new_version}.{row.extension}"

            # Construct the new filename from scratch using the table columns.
            new_file = ScienceFilePath.generate_from_inputs(
                instrument=row.instrument,
                data_level=row.data_level,
                descriptor=row.descriptor,
                start_time=row.start_date.strftime("%Y%m%d"),
                major_version=row.major_version,
                minor_version=row.minor_version,
                extension=row.extension,
                repointing=row.repointing,
                cr=row.cr,
            )

            # construct_path() prepends DATA_DIR; strip it
            new_file_path = str(new_file.construct_path().relative_to(data_dir))
            old_file_path = new_file_path[: -len(new_suffix)] + old_suffix

            basename_map[os.path.basename(old_file_path)] = os.path.basename(
                new_file_path
            )

            if new_file_path.endswith(".cdf") or new_file_path.endswith(".pkts"):
                n_cdfs_pkts += 1
                if MAX_CDFS_PKTS is None or n_cdfs_pkts <= MAX_CDFS_PKTS:
                    path_mapping[(old_file_path, old_version)] = (
                        new_file_path,
                        new_version,
                    )

        if REVERSE:
            rename_map = {v: k for k, v in path_mapping.items()}
            basename_map = {v: k for k, v in basename_map.items()}
        else:
            rename_map = dict(path_mapping)

        for (src_path, _), (dst_path, _) in rename_map.items():
            logger.info(f"Mapping {src_path} -> {dst_path}")

        dst_paths = list(rename_map.values())
        assert len(set(dst_paths)) == len(dst_paths), "Duplicates in dst_paths!"

        if copy_files:
            client = boto3.client("s3")
            for (src_path, _), (dst_path, dst_version) in rename_map.items():
                if src_path == dst_path:
                    logger.info(f"Identical src/dst: {src_path}")
                    continue
                if src_path not in s3_keys:
                    logger.info(f"Cannot read missing object: {src_path}")
                    continue

                dst_key = f"{DEST_PREFIX}{dst_path}"
                try:
                    upload_cdf(
                        client, bucket, src_path, dst_key, dst_version, basename_map
                    )
                except Exception as e:
                    logger.info(f"Failed to copy {src_path} -> {dst_key} - {e}")
                    continue
            logger.info("All destination files written")

        # Updating rows does not use `dst_key` at all. It is assumed that after making
        # a backup of the `imap/` path in the S3 bucket, files will be moved from
        # DEST_PREFIX to the original path in bulk, and then this block will be run.
        if modify_rows:
            for (src_path, _), (dst_path, _) in rename_map.items():
                session.query(models.ScienceFiles).filter(
                    models.ScienceFiles.file_path == src_path
                ).update(
                    {models.ScienceFiles.file_path: dst_path},
                    synchronize_session=False,
                )
            session.commit()
            logger.info(f"Updated file_path for {len(rename_map)} records")


if __name__ == "__main__":
    copy_files = os.getenv("COPY_FILES", "0") == "1"
    modify_rows = os.getenv("MODIFY_ROWS", "0") == "1"
    migrate(copy_files=copy_files, modify_rows=modify_rows)
