"""Migration script for renaming science files in S3/DB."""

import logging
import multiprocessing as mp
import os
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import boto3
import imap_data_access
from imap_data_access.file_validation import ScienceFilePath, Version
from imap_processing.cdf.utils import load_cdf
from imap_processing.cdf.utils import write_cdf as _write_cdf

from sds_data_manager.lambda_code.SDSCode.database import database as db
from sds_data_manager.lambda_code.SDSCode.database import models

# Destination prefix for copied files (e.g. "renamed/")
DEST_PREFIX: str = os.getenv("DEST_PREFIX", "renamed/")
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
    logger.info(f"Parents: {parents}")
    if parents is None:
        return
    if isinstance(parents, str):
        parents = [parents]

    # verbose, but we need the logging
    new_parents = []
    for p in parents:
        if p in basename_map:
            logger.info(f"Remapping parent {p} to {basename_map[p]}")
            new_parents.append(basename_map[p])
        else:
            new_parents.append(p)
    dataset.attrs["Parents"] = new_parents


def upload_cdf(
    client,
    bucket: str,
    src_key: str,
    dst_key: str,
    dst_version: str,
    basename_map: dict[str, str],
    overwrite: bool = False,
):
    """Download/modify/upload a pkts/cdf file on S3."""
    if not overwrite:
        try:
            client.head_object(Bucket=bucket, Key=dst_key)
        except client.exceptions.ClientError as e:
            code = e.response.get("Error", {}).get("Code")
            if code not in ("404", "NoSuchKey", "NotFound"):
                raise
        else:
            logger.info(f"Target exists, leaving untouched: s3://{bucket}/{dst_key}")
            return

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

    # `Logical_file_id` must match the renamed filename, sans extension.
    dataset.attrs["Logical_file_id"] = Path(dst_key).stem
    logger.info(f"Logical_file_id = {dataset.attrs['Logical_file_id']}")

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


# --- Parallel copy workers -------------------------------------------------
# Each pool worker holds its own boto3 client (clients must not be shared
# across processes) plus the read-only state every copy needs. Populated once
# per process by the pool initializer, then reused for every task.
_WORKER: dict = {}


def _init_worker(bucket: str, basename_map: dict[str, str], overwrite: bool):
    """Set up per-process state for the copy pool."""
    _WORKER["client"] = boto3.client("s3")
    _WORKER["bucket"] = bucket
    _WORKER["basename_map"] = basename_map
    _WORKER["overwrite"] = overwrite


def _copy_one(task: tuple[str, str, str]) -> tuple[str, str, str | None]:
    """Copy one file in a worker; return ``(src, dst, error_or_None)``."""
    src_path, dst_key, dst_version = task
    try:
        upload_cdf(
            _WORKER["client"],
            _WORKER["bucket"],
            src_path,
            dst_key,
            dst_version,
            _WORKER["basename_map"],
            overwrite=_WORKER["overwrite"],
        )
        return src_path, dst_key, None
    except Exception as e:
        return src_path, dst_key, str(e)


def get_s3_keys(bucket, prefix="imap/"):
    """Return the set of all object keys in ``bucket`` under ``prefix``."""
    client = boto3.client("s3")
    paginator = client.get_paginator("list_objects_v2")
    keys = set()
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        keys.update(obj["Key"] for obj in page.get("Contents", []))
    return keys


def get_existing_new_files(bucket, prefix="imap/"):
    """Return the set of all object keys in ``bucket`` under ``prefix``."""
    client = boto3.client("s3")
    paginator = client.get_paginator("list_objects_v2")
    keys = set()
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        keys.update(
            obj["Key"] for obj in page.get("Contents", []) if "v001.0" in obj["Key"]
        )
    return keys


def compute_paths(row, data_dir):
    """Return ``(old_path, old_version, new_path, new_version)`` for a DB row.

    Paths are relative to ``data_dir``. The new filename is constructed from
    the table columns; the old path differs only in the version suffix (the
    old names lacked the major version).
    """
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
    return old_file_path, old_version, new_file_path, new_version


def migrate(  # noqa: PLR0912, PLR0915
    copy_files: bool = False,
    modify_rows: bool = False,
    overwrite: bool = False,
    max_files: int = 0,
    max_workers: int = 0,
):
    """Migrate science files in S3 or update the database."""
    assert not all([copy_files, modify_rows]), "Please do this in stages!"

    data_dir = imap_data_access.config["DATA_DIR"]

    bucket = os.getenv("S3_BUCKET")
    if not bucket:
        raise ValueError("S3_BUCKET environment variable is not set")
    s3_keys: set[str] = set()
    if copy_files:
        logger.info(f"Listing objects in s3://{bucket}/imap/ ...")
        s3_keys = get_s3_keys(bucket)
        logger.info(f"Found {len(s3_keys)} objects in the bucket")

    with db.Session() as session:
        count = session.query(models.ScienceFiles).count()
        logger.info(f"Verifying file_path mapping for {count} records")

        # old_basename => new_basename, covering ALL rows (not just the ones
        # copied this run) so the `Parents` attribute can be fully remapped.
        basename_map: dict[str, str] = {}
        for row in session.query(models.ScienceFiles):
            old_file_path, _, new_file_path, _ = compute_paths(row, data_dir)
            basename_map[os.path.basename(old_file_path)] = os.path.basename(
                new_file_path
            )

        # Destinations already copied under DEST_PREFIX. Skipping these lets
        # repeated copy runs advance through the full set, max_files at a time,
        # instead of re-copying the same first N. Only meaningful when copying
        # and not force-overwriting (overwrite deliberately re-copies existing).
        existing_dsts: set[str] = set()
        if copy_files and not overwrite:
            existing_dsts = get_existing_new_files(bucket, prefix=DEST_PREFIX)
            logger.info(f"Found {len(existing_dsts)} objects under {DEST_PREFIX}")

        # Candidate CDF/PKTS rows, deterministically ordered so the "next N not
        # yet copied" is well-defined and reproducible across runs.
        candidates = (
            session.query(models.ScienceFiles)
            .filter(models.ScienceFiles.extension.in_(["cdf", "pkts"]))
            .order_by(models.ScienceFiles.file_path)
        )

        # (current_path, current_version_str) => (new_path, new_version_str),
        # skipping files already present under DEST_PREFIX and capping the
        # selection at max_files rows (0 => no cap).
        path_mapping: dict[tuple[str, str], tuple[str, str]] = {}
        for row in candidates:
            old_file_path, old_version, new_file_path, new_version = compute_paths(
                row, data_dir
            )
            # Path actually written under DEST_PREFIX (old vs new per REVERSE).
            dst_path = old_file_path if REVERSE else new_file_path
            if f"{DEST_PREFIX}{dst_path}" in existing_dsts:
                continue
            path_mapping[(old_file_path, old_version)] = (new_file_path, new_version)
            if not modify_rows and max_files != 0 and len(path_mapping) >= max_files:
                break

        if REVERSE:
            rename_map = {v: k for k, v in path_mapping.items()}
            basename_map = {v: k for k, v in basename_map.items()}
        else:
            rename_map = dict(path_mapping)

        # for (src_path, _), (dst_path, _) in rename_map.items():
        #    logger.info(f"Mapping {src_path} -> {dst_path}")

        dst_paths = list(rename_map.values())
        assert len(set(dst_paths)) == len(dst_paths), "Duplicates in dst_paths!"

        if copy_files:
            # Build the task list first (cheap, serial), skipping no-ops so the
            # workers only ever do real copies.
            tasks: list[tuple[str, str, str]] = []
            for (src_path, _), (dst_path, dst_version) in rename_map.items():
                if src_path == dst_path:
                    logger.info(f"Identical src/dst: {src_path}")
                    continue
                if src_path not in s3_keys:
                    logger.info(f"Cannot read missing object: {src_path}")
                    continue
                tasks.append((src_path, f"{DEST_PREFIX}{dst_path}", dst_version))

            # Each copy downloads, rewrites and re-uploads a CDF (CPU + I/O
            # heavy), so fan the work across processes to use all cores. A
            # `spawn` context gives each worker a clean interpreter with no
            # inherited boto3/DB sockets; workers never touch the DB.
            if tasks:
                workers = max_workers if max_workers > 0 else (os.cpu_count() or 1)
                workers = max(1, min(workers, len(tasks)))
                logger.info(f"Copying {len(tasks)} files across {workers} workers")
                with ProcessPoolExecutor(
                    max_workers=workers,
                    mp_context=mp.get_context("spawn"),
                    initializer=_init_worker,
                    initargs=(bucket, basename_map, overwrite),
                ) as executor:
                    for src_path, dst_key, err in executor.map(_copy_one, tasks):
                        if err:
                            logger.info(
                                f"Failed to copy {src_path} -> {dst_key} - {err}"
                            )
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
    overwrite = os.getenv("OVERWRITE", "0") == "1"
    max_files = int(os.getenv("MAX_FILES", "0"))
    max_workers = int(os.getenv("MAX_WORKERS", "0"))
    migrate(
        copy_files=copy_files,
        modify_rows=modify_rows,
        overwrite=overwrite,
        max_files=max_files,
        max_workers=max_workers,
    )
