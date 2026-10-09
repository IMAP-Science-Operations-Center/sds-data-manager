"""Contains all functions needed to calculate spin file dependencies."""

import datetime
import logging
from collections import defaultdict
from contextlib import nullcontext
from dataclasses import dataclass
from os.path import basename
from typing import Self

from sqlalchemy import and_

from sds_data_manager.lambda_code.SDSCode.database import database as db
from sds_data_manager.lambda_code.SDSCode.database import models

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def verify_spin_coverage(
    records: list,
    start_date: datetime,
    end_date: datetime,
) -> bool:
    """Verify that spin files cover the entire date range without gaps.

    Spin files have start_date and end_date ranges. This function verifies:
    1. First record covers or starts before the input start_date
    2. No gaps exist between consecutive record ranges
    3. Last record covers up to or past the input end_date

    If gaps are found, they are logged at INFO level.

    Parameters
    ----------
    records : list
        List of SpinFiles records with file_path, start_date, end_date.
    start_date : datetime
        Expected coverage start date.
    end_date : datetime
        Expected coverage end date.

    Returns
    -------
    bool
        True if coverage is complete, False if gaps exist.
    """
    if not records:
        logger.info(f"No spin files found for {start_date} to {end_date}")
        return False

    # Sort records by start_date
    sorted_records = sorted(records, key=lambda r: r.start_date)

    # Check if first record covers or starts before input start_date
    if sorted_records[0].start_date.replace(
        tzinfo=datetime.timezone.utc
    ) > start_date.replace(tzinfo=datetime.timezone.utc):
        gap_start = start_date
        gap_end = sorted_records[0].start_date - datetime.timedelta(days=1)
        logger.info(
            f"Spin coverage gap at start: Gap from {gap_start.strftime('%Y%m%d')} "
            f"to {gap_end.strftime('%Y%m%d')}"
        )
        return False

    # Check for gaps between consecutive records
    for i in range(len(sorted_records) - 1):
        current_end = sorted_records[i].end_date
        next_start = sorted_records[i + 1].start_date

        # Gap exists if next_start is after current_end
        # (next_start must be on the same day as current_end or overlap)
        if next_start > current_end:
            gap_start = current_end + datetime.timedelta(days=1)
            gap_end = next_start - datetime.timedelta(days=1)
            logger.info(
                f"Spin coverage gap between records: Gap from "
                f"{gap_start.strftime('%Y%m%d')} to {gap_end.strftime('%Y%m%d')}"
            )
            return False

    # Check if last record covers past input end_date
    if sorted_records[-1].end_date.replace(
        tzinfo=datetime.timezone.utc
    ) < end_date.replace(tzinfo=datetime.timezone.utc):
        gap_start = sorted_records[-1].end_date + datetime.timedelta(days=1)
        gap_end = end_date
        logger.info(
            f"Spin coverage gap at end: Gap from {gap_start.strftime('%Y%m%d')} "
            f"to {gap_end.strftime('%Y%m%d')}"
        )
        return False

    logger.info(
        f"Spin coverage verified for {start_date.strftime('%Y%m%d')} to "
        f"{end_date.strftime('%Y%m%d')}: {len(records)} file(s) cover range"
    )
    return True


@dataclass(frozen=True, slots=True, order=True)
class CoverageInterval:
    """Class to emulate a (subclassable) NamedTuple of start and end timestamps."""

    start: datetime.date
    end: datetime.date

    @classmethod
    def merge_sorted(cls, ivls: list[Self]) -> list[Self]:
        """Return a list of merged intervals."""
        ret: list[Self] = []
        prev: Self | None = None
        for current in ivls:
            if not prev:
                prev = current
                continue
            if current.start <= prev.end:
                prev = cls(prev.start, max(prev.end, current.end))
            else:
                ret.append(prev)
                prev = current
        if prev is not None:
            ret.append(prev)
        return ret

    def bounds_le(self, other: Self) -> bool:
        """Return whether self's bounds as a whole are less or equal to other's.

        Only true when self and other are disjoint, up to possible endpoint overlap.
        """
        return self.end <= other.start

    def bounds_ge(self, other: Self) -> bool:
        """Return whether self's bounds as a whole are greater or equal to other's.

        Only true when self and other are disjoint, up to possible endpoint overlap.
        """
        return self.start >= other.end

    def envelops(self, other: Self) -> bool:
        """Return whether iff self completely covers other."""
        return self.start <= other.start and self.end >= other.end


@dataclass(frozen=True, slots=True)
class RawCoverageInterval(CoverageInterval):
    """CoverageInterval that also points to the raw record from which it was derived."""

    record: models.SpinFiles
    index: int


def _filter_superseded(records: list[models.SpinFiles]) -> list[models.SpinFiles]:
    # ingest, sort, and merge coverage intervals
    raw_cov_ivls_by_version: dict[int, list[RawCoverageInterval]] = defaultdict(list)
    merged_cov_ivls_by_version: dict[int, list[CoverageInterval]] = {}
    for index, record in enumerate(records):
        interval = RawCoverageInterval(
            record.start_date.date(),
            record.end_date.date(),
            record,
            index,
        )
        raw_cov_ivls_by_version[int(record.version)].append(interval)
    for version, cov_ivls in raw_cov_ivls_by_version.items():
        cov_ivls.sort()
        merged_cov_ivls_by_version[version] = CoverageInterval.merge_sorted(cov_ivls)

    # sort the dict by descending keys (versions)
    merged_cov_ivls_by_version = dict(
        sorted(merged_cov_ivls_by_version.items(), reverse=True)
    )

    # find merged coverage intervals at higher priority than each version
    higher_cov_ivls_by_version: dict[int, list[CoverageInterval]] = {}
    next_higher_version: int | None = None
    for version, cov_ivls in merged_cov_ivls_by_version.items():
        if next_higher_version is None:
            higher_cov_ivls_by_version[version] = []
            next_higher_version = version
            continue
        combined_ivls = cov_ivls + higher_cov_ivls_by_version[next_higher_version]
        combined_ivls.sort()
        merged_combined_ivls = CoverageInterval.merge_sorted(combined_ivls)
        higher_cov_ivls_by_version[version] = merged_combined_ivls
        next_higher_version = version

    filtered_raw_ivls: list[RawCoverageInterval] = []
    for version, raw_cov_ivls in raw_cov_ivls_by_version.items():
        # cov_ivls is already sorted by start/end
        higher_cov_ivls = higher_cov_ivls_by_version[version]

        higher_iter = iter(higher_cov_ivls)
        current_higher = next(higher_iter, None)
        for current_raw in raw_cov_ivls:
            while current_higher is not None:
                if current_raw.bounds_le(current_higher):
                    # save current_raw
                    filtered_raw_ivls.append(current_raw)
                    break
                elif current_raw.bounds_ge(current_higher):
                    # increment current_higher
                    current_higher = next(higher_iter, None)
                # current_raw and current_higher intersect
                elif current_higher.envelops(current_raw):
                    # throw away current_raw, as it's completely covered
                    break
                else:
                    # save current_raw, as it is only partially covered
                    # by higher priority intervals
                    filtered_raw_ivls.append(current_raw)
                    break
            else:
                # save current_raw, as its bounds are greater than all
                # higher priority intervals
                filtered_raw_ivls.append(current_raw)

    # sort by the input ordering
    filtered_raw_ivls.sort(key=lambda raw_ivl: raw_ivl.index)

    return [raw_ivl.record for raw_ivl in filtered_raw_ivls]


def get_spin_files(
    session,
    start_date: datetime,
    end_date: datetime,
) -> list:
    """Get spin input.

    Query the spin table for the given date range and drop any file whose
    days in the range are all covered by higher-version files.

    Parameters
    ----------
    session : orm session
        Database session.
    start_date : datetime
        Start date to find dependent files with.
    end_date : datetime
        End date to find dependent files with.

    Returns
    -------
    list
        List of SpinFiles records with file_path, start_date, end_date, version,
        in reverse priority order: lowest version first, then earliest start
        date first, so the last record has the highest priority.
    """
    spin = models.SpinFiles
    candidates = (
        session.query(
            spin.file_path,
            spin.start_date,
            spin.end_date,
            spin.version,
        )
        .filter(
            and_(
                spin.start_date <= end_date,
                spin.end_date >= start_date,
            )
        )
        # Order by ingestion date, oldest first
        .order_by(spin.ingestion_date)
        .all()
    )

    records = _filter_superseded(candidates)
    # imap_processing gives the last file the highest priority. The sort is
    # stable, so ingestion order breaks ties in version and start date.
    records.sort(key=lambda record: (int(record.version), record.start_date))
    return records


def get_upstream_dependency_inputs_spin(
    start_date: datetime,
    end_date: datetime,
    require_coverage: bool = False,
    open_session: db.Session = None,
):
    """Construct a ProcessingInputCollection of dependency files.

    For each dependency, query for existing files in s3 and add any matching files
    found to a ProcessingInputCollection.

    Parameters
    ----------
    dependencies : list
        List of dependency dictionaries either downstream or upstream from the
        dependency in the query parameters.
    start_date : datetime
        Start date to find dependent files with.
    end_date : datetime
        End date to find dependent files with.
    repoint : int or list[int], optional
        If provided, will be used to filter files by repoint number(s). Can be a
        single int or a list of ints.
    require_coverage : bool, optional
        If True gathered dependencies will be checked for complete coverage of
        start_date to end_date or repoint coverage.
    open_session : db.Session, optional
        Database session. If not provided, a new session will be created.

    Returns
    -------
    ProcessingInputCollection
        Dependency files that can include Ancillary, SPICE, or Science inputs.
    """
    # Use provided session or create a new one
    session_context = nullcontext(open_session) if open_session else db.Session()
    with session_context as session:
        spin_records = get_spin_files(session, start_date, end_date)
        if not spin_records:
            logger.info(f"No spin files found for {start_date} to {end_date}")
            return None
        # Verify spin coverage
        if require_coverage and not verify_spin_coverage(
            spin_records, start_date, end_date
        ):
            return None
        spin_files = [basename(record.file_path) for record in spin_records]
        logger.info(f"Found spin files: {spin_files}. Adding to collection.")

    return spin_files
