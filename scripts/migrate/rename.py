# ruff: noqa

import logging
import multiprocessing as mp
import os
import re
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

# NB: spacepy is imported lazily (inside `upgrade_file`), never at module top.
# `import spacepy` bootstraps a per-user config file ($SPACEPY/.spacepy/spacepy.rc)
# via a non-atomic read-modify-write. Under the `spawn` pool every worker imports
# the module fresh, and concurrent bootstraps race on that shared file -- one
# worker's partial write is another's corrupt read, crashing with
# `UnboundLocalError: nextsec` -> BrokenProcessPool. Deferring the import lets
# `_init_worker` point each worker at its own $SPACEPY dir first, so there is no
# shared config file to race on.

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(message)s")

MAJOR_VERSION = 1

_OLD_SUFFIX_RE = re.compile(r"_v(\d{3})\.(cdf|pkts)$")

# Ancillary parents (e.g. imap_mag_l1b-calibration_20240229_v001.cdf) keep
# vNNN naming and must pass through unchanged (see #1538).
_OLD_SCIENCE_RE = re.compile(
    r"^imap_[a-z0-9]+_l\d[a-z]?_[a-zA-Z0-9-]+_\d{8}(?:-repoint\d+)?_v\d{3}\.(cdf|pkts)$"
)


def make_new_file_name(name: str, major: int = MAJOR_VERSION) -> str:
    replacement = r"_v%03d.0\1.\2" % major
    return _OLD_SUFFIX_RE.sub(replacement, name)


def upgrade_file(
    original_file: Path, working_dir: Path, major: int = MAJOR_VERSION
) -> Path:
    original_file = Path(original_file)
    match = _OLD_SUFFIX_RE.search(original_file.name)
    if match is None:
        raise ValueError(f"Not a legacy-versioned science file: {original_file.name}")
    old_version = match[1]  # e.g. "005"
    new_version = f"{major:03d}.{int(old_version):04d}"  # e.g. "001.0005"

    new_name = make_new_file_name(original_file.name, major)
    out_path = Path(working_dir) / new_name

    # Lazy import: see the note at the top of this module. In the pool this runs
    # after `_init_worker` has isolated $SPACEPY; standalone/sequential callers
    # just import it here in-process (no concurrency, no race).
    from spacepy.pycdf import CDF

    with CDF(str(out_path), masterpath=str(original_file)) as cdf:
        # `Data_version` is stored without the leading `v`.
        cdf.attrs["Data_version"] = new_version

        # `Logical_file_id` must match the renamed filename, sans extension.
        if "Logical_file_id" in cdf.attrs and len(cdf.attrs["Logical_file_id"]):
            logical_file_id = str(cdf.attrs["Logical_file_id"][0])
            cdf.attrs["Logical_file_id"] = logical_file_id.replace(
                f"_v{old_version}", f"_v{new_version}"
            )

        if "File_naming_convention" in cdf.attrs:
            cdf.attrs["File_naming_convention"] = (
                "source_descriptor_datatype_yyyyMMdd_vMMM.mmmm"
            )

        if "Parents" in cdf.attrs:
            parents = [str(p) for p in cdf.attrs["Parents"]]
            cdf.attrs["Parents"] = [
                make_new_file_name(p, major) if _OLD_SCIENCE_RE.match(p) else p
                for p in parents
            ]

    return out_path


# --- S3 listing ------------------------------------------------------------
def list_keys(bucket: str, prefix: str) -> list[str]:
    """Return all object keys in ``bucket`` under ``prefix``."""
    client = boto3.client("s3")
    paginator = client.get_paginator("list_objects_v2")
    keys: list[str] = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        keys.extend(obj["Key"] for obj in page.get("Contents", []))
    return keys


# --- worker pool -----------------------------------------------------------
# Per-process state. We deliberately do NOT pickle a boto3 client (or anything
# holding native/OpenSSL handles) across the process boundary: each worker
# builds its own in `_init_worker`. Combined with the `spawn` start method (see
# `rename`), this guarantees no half-initialized C-library state -- libcdf or
# boto3's SSL sockets -- is inherited across a fork, which is the actual cause
# of the intermittent "spacepy crashing under multiprocessing" segfaults.
_WORKER: dict = {}


def _init_worker(src_bucket: str, dst_bucket: str, major: int) -> None:
    """Set up per-process state for the rename pool."""
    # Give this worker its own spacepy config dir *before* spacepy is ever
    # imported, so the workers never share (and race on) a single
    # ~/.spacepy/spacepy.rc. spacepy reads $SPACEPY/.spacepy/spacepy.rc; a unique
    # per-process dir makes the config bootstrap contention-free. Importing
    # spacepy here (single-threaded within this worker) bootstraps it once.
    spacepy_home = tempfile.mkdtemp(prefix=f"spacepy-{os.getpid()}-")
    os.environ["SPACEPY"] = spacepy_home
    import spacepy.pycdf  # noqa: F401  (force the isolated config bootstrap now)

    _WORKER["client"] = boto3.client("s3")
    _WORKER["src_bucket"] = src_bucket
    _WORKER["dst_bucket"] = dst_bucket
    _WORKER["major"] = major


def _process_one_worker(task: tuple[str, str]) -> tuple[str, str, str | None]:
    """Rename one object using this worker's own client (pool entry point)."""
    return _process_one(
        _WORKER["client"],
        _WORKER["src_bucket"],
        _WORKER["dst_bucket"],
        task,
        _WORKER["major"],
    )


def _process_one(
    client, src_bucket: str, dst_bucket: str, task: tuple[str, str], major: int
) -> tuple[str, str, str | None]:
    """Rename one object; return ``(src, dst, error_or_None)``."""
    src_key, dst_key = task
    try:
        if src_key.endswith(".pkts"):
            # No metadata to rewrite - a server-side cross-bucket copy suffices.
            client.copy_object(
                Bucket=dst_bucket,
                CopySource={"Bucket": src_bucket, "Key": src_key},
                Key=dst_key,
            )
            return src_key, dst_key, None

        # CDF: download, rewrite attributes in place, upload the renamed copy.
        with tempfile.TemporaryDirectory() as workdir:
            local_src = Path(workdir) / Path(src_key).name
            client.download_file(src_bucket, src_key, str(local_src))
            out_path = upgrade_file(local_src, workdir, major)
            client.upload_file(str(out_path), dst_bucket, dst_key)
        return src_key, dst_key, None
    except Exception as e:
        return src_key, dst_key, f"ERROR: {e}"


def rename(
    src_bucket: str,
    dst_bucket: str,
    prefix: str = "imap/",
    overwrite: bool = False,
    max_files: int = 0,
    max_workers: int = 0,
    dry_run: bool = False,
    major: int = MAJOR_VERSION,
):
    """Rename science files from ``src_bucket`` to ``dst_bucket``."""
    if dst_bucket == src_bucket:
        raise ValueError("dst_bucket must differ from src_bucket")

    # S3 prefixes match by raw string
    # Let the caller explicitly choose "imap/hi/" vs "imap/hit/" to avoid confusion
    if not prefix.endswith("/"):
        raise ValueError(
            f"prefix must end with '/' to keep shards disjoint (got {prefix!r}); "
            f"did you mean {prefix + '/'!r}?"
        )

    logger.info(f"Listing s3://{src_bucket}/{prefix} ...")
    src_keys = sorted(list_keys(src_bucket, prefix))
    logger.info(f"Found {len(src_keys)} source objects")

    # Destinations already written. Skipping these (unless overwriting) lets
    # repeated runs advance through the whole set, max_files at a time, instead
    # of re-processing the same first N.
    existing_dst: set[str] = set()
    if not overwrite:
        try:
            existing_dst = set(list_keys(dst_bucket, prefix))
            logger.info(f"Found {len(existing_dst)} objects already in destination")
        except ClientError as e:
            if not dry_run:
                raise
            code = e.response.get("Error", {}).get("Code")
            logger.info(
                f"Destination s3://{dst_bucket}/{prefix} not listable ({code}); "
                f"assuming empty (dry run)"
            )

    tasks: list[tuple[str, str]] = []
    for key in src_keys:
        base = os.path.basename(key)
        new_base = make_new_file_name(base, major)
        if new_base == base:
            # Not a legacy-versioned science file (already-new, ancillary,
            # spice, dependency, ...): leave it alone.
            continue
        dst_key = key[: len(key) - len(base)] + new_base
        if not overwrite and dst_key in existing_dst:
            continue
        tasks.append((key, dst_key))
        if max_files and len(tasks) >= max_files:
            break

    logger.info(f"{len(tasks)} files to process")
    if not tasks:
        return

    if dry_run:
        for src_key, dst_key in tasks:
            logger.info(f"{src_key} -> {dst_key}")
        logger.info(f"Dry run: {len(tasks)} files would be renamed (nothing written)")
        return

    # Each file downloads, rewrites and re-uploads a CDF (I/O heavy), so fan the
    # work across processes. A `spawn` context gives every worker a clean
    # interpreter that imports spacepy/boto3 fresh -- nothing native is inherited
    # across a fork, which is what made spacepy segfault under multiprocessing.
    workers = max_workers if max_workers > 0 else (os.cpu_count() or 1)
    workers = max(1, min(workers, len(tasks)))
    ok = fail = 0

    if workers == 1:
        # Single worker: stay in-process, no pool overhead.
        logger.info(f"Processing {len(tasks)} files sequentially")
        client = boto3.client("s3")
        results = (
            _process_one(client, src_bucket, dst_bucket, task, major) for task in tasks
        )
    else:
        logger.info(f"Processing {len(tasks)} files across {workers} workers")
        executor = ProcessPoolExecutor(
            max_workers=workers,
            mp_context=mp.get_context("spawn"),
            initializer=_init_worker,
            initargs=(src_bucket, dst_bucket, major),
        )
        results = executor.map(_process_one_worker, tasks)

    try:
        for src_key, dst_key, err in results:
            if err:
                fail += 1
                logger.info(f"FAIL {src_key} -> {dst_key}: {err}")
            else:
                ok += 1
                logger.info(f"OK   {src_key} -> {dst_key}")
    finally:
        if workers != 1:
            executor.shutdown()
    logger.info(f"Done: {ok} succeeded, {fail} failed")


if __name__ == "__main__":
    src_bucket = os.getenv("SRC_BUCKET")
    dst_bucket = os.getenv("DST_BUCKET")
    if not src_bucket or not dst_bucket:
        raise SystemExit("SRC_BUCKET and DST_BUCKET must both be set")
    rename(
        src_bucket=src_bucket,
        dst_bucket=dst_bucket,
        prefix=os.getenv("SRC_PREFIX", "imap/"),
        overwrite=os.getenv("OVERWRITE", "0") == "1",
        max_files=int(os.getenv("MAX_FILES", "0")),
        max_workers=int(os.getenv("MAX_WORKERS", "0")),
        dry_run=os.getenv("DRY_RUN", "0") == "1",
    )
