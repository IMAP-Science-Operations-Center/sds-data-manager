"""Tests for dynamic partition sensors in custom_partitions."""

import datetime
import itertools
from unittest import mock
from unittest.mock import MagicMock, patch

from dagster import (
    DagsterInstance,
    DeleteDynamicPartitionsRequest,
    build_sensor_context,
    instance_for_test,
)

from sds_data_manager.lambda_code.SDSCode.database import database as db
from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.orchestration import custom_partitions
from sds_data_manager.orchestration.custom_partitions import (
    add_pointing_attitude_partitions,
)
from sds_data_manager.orchestration.maps_utils import (
    FIRST_MAP_START_DATE,
    get_map_partition_names,
)
from tests.orchestration.conftest import insert_ah_kernel


@mock.patch("sds_data_manager.orchestration.custom_partitions.datetime")
def test_add_idex_10_day_partitions(mock_datetime):
    """Check that add_idex_10_day_partitions adds the correct partitions."""
    mock_datetime.datetime.now.return_value = datetime.datetime(
        2025, 9, 29, tzinfo=datetime.timezone.utc
    )
    mock_datetime.datetime.fromisoformat.return_value = datetime.datetime(
        2025, 9, 24, tzinfo=datetime.timezone.utc
    )
    mock_datetime.timezone = datetime.timezone
    mock_datetime.timedelta = datetime.timedelta
    with instance_for_test() as instance:
        # Mock existing partitions
        instance.add_dynamic_partitions(
            "idex_10_day_partitions",
            ["idex10_2025-09-27T00:00:00_to_2025-10-07T00:00:00"],
        )
        context = build_sensor_context(instance=instance)
        # Trigger the sensor. This should add more partitions.
        sensor_result = custom_partitions.add_idex_10_day_partitions(context)

    new_partitions = sensor_result.dynamic_partitions_requests[0].partition_keys
    assert new_partitions == [
        "idex10_2025-10-07T00:00:00_to_2025-10-17T00:00:00",
        "idex10_2025-10-17T00:00:00_to_2025-10-27T00:00:00",
        "idex10_2025-10-27T00:00:00_to_2025-11-06T00:00:00",
    ]


def test_add_cadence_map_partitions_open_window():
    """Check that get_map_partition_names includes the active open window."""
    partition_names = get_map_partition_names(
        "3mo",
        start_time=FIRST_MAP_START_DATE,
        current_time=datetime.datetime(2026, 8, 20, tzinfo=datetime.timezone.utc),
        include_open=True,
    )

    assert partition_names == [
        "cadence-3mo_2026-01-17T00:00:00_to_2026-04-18T00:00:00",
        "cadence-3mo_2026-04-18T00:00:00_to_2026-07-18T00:00:00",
        "cadence-3mo_2026-07-18T00:00:00_to_2026-10-17T00:00:00",
    ]


# ---------------------------------------------------------------------------
# add_pointing_attitude_partitions - helpers
# ---------------------------------------------------------------------------


def _dt(s):
    """Parse %Y-%m-%dT%H:%M:%S to a UTC-aware datetime."""
    return datetime.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S").replace(
        tzinfo=datetime.timezone.utc
    )


def make_coverage(min_str, max_str, file_name="imap_2025_001_2025_090_001.ah.bc"):
    """Build a single (file_name, start, end) effective-coverage stretch."""
    return (file_name, _dt(min_str), _dt(max_str))


def make_pointing(start_str, end_str):
    """Minimal mock PointingTable record."""
    p = MagicMock()
    p.pointing_start_utc = _dt(start_str)
    p.pointing_end_utc = _dt(end_str)
    return p


def _run_pointing_attitude_sensor(instance, coverage, pointing_query_results):
    """Run the sensor with mocked coverage/pointings and return the SensorResult.

    `coverage` is what _get_effective_ah_coverage returns. Each stretch issues
    two PointingTable queries (first_overlapping then last_covered), so
    pointing_query_results is a flat list in chronological stretch order:
    [first_overlapping_0, last_covered_0, first_overlapping_1, ...]
    """
    mock_session = MagicMock()
    pointing_query = MagicMock()
    pointing_query.filter.return_value.order_by.return_value.first.side_effect = (
        pointing_query_results
    )
    mock_session.query.return_value = pointing_query

    context = build_sensor_context(instance=instance)
    with (
        patch.object(db, "Session") as mock_cls,
        patch.object(
            custom_partitions, "_get_effective_ah_coverage", return_value=coverage
        ),
    ):
        mock_cls.return_value.__enter__.return_value = mock_session
        mock_cls.return_value.__exit__.return_value = False
        return add_pointing_attitude_partitions(context)


# ---------------------------------------------------------------------------
# add_pointing_attitude_partitions - tests
# ---------------------------------------------------------------------------


def test_no_ah_kernels_returns_no_requests():
    """Sensor is a no-op when there is no attitude_history coverage."""
    instance = DagsterInstance.ephemeral()
    result = _run_pointing_attitude_sensor(
        instance, coverage=[], pointing_query_results=[]
    )
    assert result.dynamic_partitions_requests == []


def test_no_overlapping_pointings_creates_no_partition():
    """No partition is created when no pointings overlap with the ah coverage.

    Existing partitions must not be deleted just because nothing was
    resolvable this tick.
    """
    instance = DagsterInstance.ephemeral()
    existing = "pointingattitude_2024-01-01T00:00:00_to_2024-04-01T00:00:00"
    instance.add_dynamic_partitions("pointing_attitude_partitions", [existing])
    coverage = make_coverage("2025-01-01T00:00:00", "2025-04-01T00:00:00")

    result = _run_pointing_attitude_sensor(instance, [coverage], [None, None])

    assert result.dynamic_partitions_requests == []


def test_partial_coverage_only_creates_no_partition():
    """No partition is created when overlap exists but no pointing is fully covered."""
    instance = DagsterInstance.ephemeral()
    coverage = make_coverage("2025-01-01T00:00:00", "2025-04-01T00:00:00")
    first = make_pointing("2025-01-01T00:00:00", "2025-01-15T00:00:00")
    result = _run_pointing_attitude_sensor(instance, [coverage], [first, None])
    assert result.dynamic_partitions_requests == []


def test_new_partition_created():
    """A new partition is added when none exists for the coverage."""
    instance = DagsterInstance.ephemeral()
    coverage = make_coverage("2025-01-01T00:00:00", "2025-04-01T00:00:00")
    first = make_pointing("2025-01-01T00:00:00", "2025-01-15T00:00:00")
    last = make_pointing("2025-03-15T00:00:00", "2025-04-01T00:00:00")

    result = _run_pointing_attitude_sensor(instance, [coverage], [first, last])

    assert len(result.dynamic_partitions_requests) == 1
    assert result.dynamic_partitions_requests[0].partition_keys == [
        "pointingattitude_2025-01-01T00:00:00_to_2025-04-01T00:00:00"
    ]


def test_already_up_to_date_creates_no_requests():
    """No requests are made when the exact partition already exists."""
    instance = DagsterInstance.ephemeral()
    existing = "pointingattitude_2025-01-01T00:00:00_to_2025-04-01T00:00:00"
    instance.add_dynamic_partitions("pointing_attitude_partitions", [existing])

    coverage = make_coverage("2025-01-01T00:00:00", "2025-04-01T00:00:00")
    first = make_pointing("2025-01-01T00:00:00", "2025-01-15T00:00:00")
    last = make_pointing("2025-03-15T00:00:00", "2025-04-01T00:00:00")

    result = _run_pointing_attitude_sensor(instance, [coverage], [first, last])

    assert result.dynamic_partitions_requests == []


def test_growing_append_replaces_existing_partition():
    """When the ah coverage extends its end date, the stale partition is replaced.

    This is the normal appending case: a kernel with the same start date grows
    its end date with each new delivery.
    """
    instance = DagsterInstance.ephemeral()
    old_key = "pointingattitude_2025-01-01T00:00:00_to_2025-02-15T00:00:00"
    instance.add_dynamic_partitions("pointing_attitude_partitions", [old_key])

    coverage = make_coverage("2025-01-01T00:00:00", "2025-04-01T00:00:00")
    first = make_pointing("2025-01-01T00:00:00", "2025-01-15T00:00:00")
    last = make_pointing("2025-03-15T00:00:00", "2025-04-01T00:00:00")

    result = _run_pointing_attitude_sensor(instance, [coverage], [first, last])

    # Sensor always emits delete before add
    assert len(result.dynamic_partitions_requests) == 2
    delete_req = result.dynamic_partitions_requests[0]
    add_req = result.dynamic_partitions_requests[1]
    assert delete_req.partition_keys == [old_key]
    assert add_req.partition_keys == [
        "pointingattitude_2025-01-01T00:00:00_to_2025-04-01T00:00:00"
    ]


def test_retroactive_combined_file_replaces_daily_partitions():
    """A combined ah file replaces many small early-mission daily partitions."""
    instance = DagsterInstance.ephemeral()
    old_keys = [
        "pointingattitude_2025-01-01T00:00:00_to_2025-01-15T00:00:00",
        "pointingattitude_2025-01-15T00:00:00_to_2025-01-30T00:00:00",
        "pointingattitude_2025-01-30T00:00:00_to_2025-02-14T00:00:00",
    ]
    instance.add_dynamic_partitions("pointing_attitude_partitions", old_keys)

    coverage = make_coverage("2025-01-01T00:00:00", "2025-04-01T00:00:00")
    first = make_pointing("2025-01-01T00:00:00", "2025-01-15T00:00:00")
    last = make_pointing("2025-03-15T00:00:00", "2025-04-01T00:00:00")

    result = _run_pointing_attitude_sensor(instance, [coverage], [first, last])

    assert len(result.dynamic_partitions_requests) == 2
    delete_req = result.dynamic_partitions_requests[0]
    add_req = result.dynamic_partitions_requests[1]
    assert set(delete_req.partition_keys) == set(old_keys)
    assert add_req.partition_keys == [
        "pointingattitude_2025-01-01T00:00:00_to_2025-04-01T00:00:00"
    ]


def test_superseded_partition_deleted_even_when_not_contained():
    """A partition that is no longer desired is deleted, even if it's larger.

    A reprocessed delivery can end earlier than the kernel it replaces, so the
    old partition is not contained in the new one; it must still go.
    """
    instance = DagsterInstance.ephemeral()
    old_key = "pointingattitude_2025-01-01T00:00:00_to_2025-04-01T00:00:00"
    instance.add_dynamic_partitions("pointing_attitude_partitions", [old_key])

    coverage = make_coverage("2025-01-01T00:00:00", "2025-03-01T00:00:00")
    first = make_pointing("2025-01-01T00:00:00", "2025-01-15T00:00:00")
    last = make_pointing("2025-02-15T00:00:00", "2025-03-01T00:00:00")

    result = _run_pointing_attitude_sensor(instance, [coverage], [first, last])

    delete_req, add_req = result.dynamic_partitions_requests
    assert delete_req.partition_keys == [old_key]
    assert add_req.partition_keys == [
        "pointingattitude_2025-01-01T00:00:00_to_2025-03-01T00:00:00"
    ]


def test_stretches_processed_chronologically():
    """Stretches arrive in metakernel priority order but are keyed in time order."""
    instance = DagsterInstance.ephemeral()
    later = make_coverage("2025-02-15T00:00:00", "2025-04-01T00:00:00", "b")
    earlier = make_coverage("2025-01-01T00:00:00", "2025-02-15T00:00:00", "a")

    result = _run_pointing_attitude_sensor(
        instance,
        [later, earlier],
        [
            make_pointing("2025-01-01T00:00:00", "2025-01-10T00:00:00"),
            make_pointing("2025-02-05T00:00:00", "2025-02-15T00:00:00"),
            make_pointing("2025-02-15T00:00:00", "2025-02-20T00:00:00"),
            make_pointing("2025-03-25T00:00:00", "2025-04-01T00:00:00"),
        ],
    )

    assert result.dynamic_partitions_requests[0].partition_keys == [
        "pointingattitude_2025-01-01T00:00:00_to_2025-02-15T00:00:00",
        "pointingattitude_2025-02-15T00:00:00_to_2025-04-01T00:00:00",
    ]


# ---------------------------------------------------------------------------
# add_pointing_attitude_partitions - reprocessed ah delivery (real DB)
# ---------------------------------------------------------------------------

# (file name, coverage start, coverage end) of the long-duration ah kernels in
# prod before the ACS team's reprocessed whole-mission delivery.
PROD_AH_KERNELS = [
    ("imap_2025_267_2025_358_002.ah.bc", "2025-09-24T14:06:45", "2025-12-24T18:47:06"),
    ("imap_2025_358_2026_085_004.ah.bc", "2025-12-24T17:47:07", "2026-03-26T00:17:05"),
    ("imap_2026_084_2026_175_001.ah.bc", "2026-03-25T23:17:06", "2026-06-24T14:47:06"),
    ("imap_2026_175_2026_189_002.ah.bc", "2026-06-24T13:47:07", "2026-07-08T15:50:21"),
    ("imap_2026_189_2026_278_001.ah.bc", "2026-07-08T15:20:22", "2026-10-05T14:47:06"),
]

# The reprocessed delivery. Its boundaries don't line up with the prod kernels
# it replaces, and it stops short of the end of prod coverage.
DRAFT_AH_KERNELS = [
    ("imap_2025_267_2025_358_010.ah.bc", "2025-09-24T13:36:45", "2025-12-24T19:17:05"),
    ("imap_2025_358_2026_032_010.ah.bc", "2025-12-24T18:17:06", "2026-02-01T19:17:05"),
    ("imap_2026_031_2026_085_010.ah.bc", "2026-01-31T13:36:45", "2026-03-26T14:36:44"),
    ("imap_2026_085_2026_175_010.ah.bc", "2026-03-26T13:36:45", "2026-06-24T14:36:44"),
    ("imap_2026_175_2026_189_010.ah.bc", "2026-06-24T13:36:45", "2026-07-08T16:19:59"),
    ("imap_2026_189_2026_273_010.ah.bc", "2026-07-08T15:20:00", "2026-09-30T20:02:59"),
    ("imap_2026_273_2026_275_010.ah.bc", "2026-09-30T19:03:00", "2026-10-02T12:12:06"),
]


def _insert_daily_pointings(session):
    """Insert one pointing per day, 2025-09-22 through 2026-10-10.

    Each pointing starts at 14:00, starts slewing to the next pointing at
    13:45 the following day, and ends when the next pointing starts.
    """
    day = datetime.datetime(2025, 9, 22, 14, tzinfo=datetime.timezone.utc)
    pointing_id = 0
    while day < datetime.datetime(2026, 10, 10, tzinfo=datetime.timezone.utc):
        next_day = day + datetime.timedelta(days=1)
        session.add(
            models.PointingTable(
                pointing_id=pointing_id,
                pointing_start_utc=day,
                pointing_end_utc=next_day,
                repoint_start_utc=next_day - datetime.timedelta(minutes=15),
                repoint_end_utc=next_day,
            )
        )
        pointing_id += 1
        day = next_day
    session.commit()


def _insert_kernels(session, kernels, first_ingestion):
    """Insert ah kernels, each ingested one minute after the previous one."""
    for i, (file_name, start, end) in enumerate(kernels):
        insert_ah_kernel(
            session,
            file_name,
            _dt(start),
            _dt(end),
            ingestion_date=first_ingestion + datetime.timedelta(minutes=i),
        )


def _reconcile(instance):
    """Run the sensor and apply its add/delete requests to the instance."""
    result = add_pointing_attitude_partitions(build_sensor_context(instance=instance))
    for request in result.dynamic_partitions_requests:
        if isinstance(request, DeleteDynamicPartitionsRequest):
            for key in request.partition_keys:
                instance.delete_dynamic_partition(request.partitions_def_name, key)
        else:
            instance.add_dynamic_partitions(
                request.partitions_def_name, request.partition_keys
            )
    return result


def _assert_partitions_tile(partitions):
    """Each partition must start exactly where the previous one ends."""
    ranges = sorted(key.split("_", 1)[1].split("_to_") for key in partitions)
    for (_, previous_end), (start, _) in itertools.pairwise(ranges):
        assert start == previous_end, ranges


def test_effective_ah_coverage_after_reprocessed_delivery(mock_db_session):
    """The newest delivery wins its full range; old kernels only fill the tail."""
    _insert_kernels(mock_db_session, PROD_AH_KERNELS, _dt("2026-01-01T00:00:00"))
    _insert_kernels(mock_db_session, DRAFT_AH_KERNELS, _dt("2026-10-06T00:00:00"))

    coverage = sorted(
        (name.split("/")[-1], start, end)
        for name, start, end in custom_partitions._get_effective_ah_coverage()
    )

    assert coverage == [
        (
            "imap_2025_267_2025_358_010.ah.bc",
            _dt("2025-09-24T13:36:45"),
            _dt("2025-12-24T18:17:06"),
        ),
        (
            "imap_2025_358_2026_032_010.ah.bc",
            _dt("2025-12-24T18:17:06"),
            _dt("2026-01-31T13:36:45"),
        ),
        (
            "imap_2026_031_2026_085_010.ah.bc",
            _dt("2026-01-31T13:36:45"),
            _dt("2026-03-26T13:36:45"),
        ),
        (
            "imap_2026_085_2026_175_010.ah.bc",
            _dt("2026-03-26T13:36:45"),
            _dt("2026-06-24T13:36:45"),
        ),
        (
            "imap_2026_175_2026_189_010.ah.bc",
            _dt("2026-06-24T13:36:45"),
            _dt("2026-07-08T15:20:00"),
        ),
        (
            "imap_2026_189_2026_273_010.ah.bc",
            _dt("2026-07-08T15:20:00"),
            _dt("2026-09-30T19:03:00"),
        ),
        # Old prod kernel still fills the time after the delivery ends.
        (
            "imap_2026_189_2026_278_001.ah.bc",
            _dt("2026-10-02T12:12:06"),
            _dt("2026-10-05T14:47:06"),
        ),
        (
            "imap_2026_273_2026_275_010.ah.bc",
            _dt("2026-09-30T19:03:00"),
            _dt("2026-10-02T12:12:06"),
        ),
    ]


def test_reprocessed_ah_delivery_replaces_partitions(mock_db_session):
    """A whole-mission reprocessed delivery leaves one clean chain of partitions.

    Before the fix, superseded prod kernels that weren't fully contained in a
    reprocessed kernel kept their partitions, leaving overlapping and nested
    partitions (e.g. 07-08 -> 10-05 alongside 07-08 -> 09-30).
    """
    _insert_daily_pointings(mock_db_session)
    _insert_kernels(mock_db_session, PROD_AH_KERNELS, _dt("2026-01-01T00:00:00"))

    with instance_for_test() as instance:
        _reconcile(instance)
        prod_partitions = set(
            instance.get_dynamic_partitions("pointing_attitude_partitions")
        )
        assert prod_partitions == {
            "pointingattitude_2025-09-24T14:00:00_to_2025-12-24T14:00:00",
            "pointingattitude_2025-12-24T14:00:00_to_2026-03-25T14:00:00",
            "pointingattitude_2026-03-25T14:00:00_to_2026-06-24T14:00:00",
            "pointingattitude_2026-06-24T14:00:00_to_2026-07-08T14:00:00",
            "pointingattitude_2026-07-08T14:00:00_to_2026-10-05T14:00:00",
        }
        _assert_partitions_tile(prod_partitions)

        _insert_kernels(mock_db_session, DRAFT_AH_KERNELS, _dt("2026-10-06T00:00:00"))
        _reconcile(instance)
        draft_partitions = set(
            instance.get_dynamic_partitions("pointing_attitude_partitions")
        )

        assert draft_partitions == {
            "pointingattitude_2025-09-23T14:00:00_to_2025-12-24T14:00:00",
            "pointingattitude_2025-12-24T14:00:00_to_2026-01-30T14:00:00",
            "pointingattitude_2026-01-30T14:00:00_to_2026-03-25T14:00:00",
            "pointingattitude_2026-03-25T14:00:00_to_2026-06-23T14:00:00",
            "pointingattitude_2026-06-23T14:00:00_to_2026-07-08T14:00:00",
            "pointingattitude_2026-07-08T14:00:00_to_2026-09-30T14:00:00",
            # The 2026_273_2026_275 kernel fully covers no pointing, so it is
            # merged with the prod kernel tail that follows it rather than
            # dropping the 09-30 pointing from every partition.
            "pointingattitude_2026-09-30T14:00:00_to_2026-10-05T14:00:00",
        }
        # Every prod partition was replaced; none overlap the new ones.
        assert not prod_partitions & draft_partitions
        _assert_partitions_tile(draft_partitions)

        # Stable: a second tick changes nothing.
        assert _reconcile(instance).dynamic_partitions_requests == []


def test_trailing_remainder_extends_previous_partition(mock_db_session):
    """A final stretch covering no pointing on its own still extends the chain.

    The newer kernel takes over partway through the 10-09 pointing, so neither
    kernel fully covers that pointing alone, but together they do.
    """
    _insert_daily_pointings(mock_db_session)
    insert_ah_kernel(
        mock_db_session,
        "imap_2025_274_2025_283_001.ah.bc",
        _dt("2025-10-01T00:00:00"),
        _dt("2025-10-10T00:00:00"),
        ingestion_date=_dt("2025-10-10T00:00:00"),
    )
    insert_ah_kernel(
        mock_db_session,
        "imap_2025_282_2025_283_001.ah.bc",
        _dt("2025-10-09T20:00:00"),
        _dt("2025-10-10T18:00:00"),
        ingestion_date=_dt("2025-10-11T00:00:00"),
    )

    with instance_for_test() as instance:
        instance.add_dynamic_partitions(
            "pointing_attitude_partitions",
            ["pointingattitude_2025-09-30T14:00:00_to_2025-10-10T14:00:00"],
        )
        # The existing partition is already correct, so it is kept as is.
        assert _reconcile(instance).dynamic_partitions_requests == []


def test_trailing_remainder_after_gap_not_merged(mock_db_session):
    """A trailing stretch separated from the previous one by a gap is dropped."""
    _insert_daily_pointings(mock_db_session)
    insert_ah_kernel(
        mock_db_session,
        "imap_2025_274_2025_282_001.ah.bc",
        _dt("2025-10-01T00:00:00"),
        _dt("2025-10-09T20:00:00"),
        ingestion_date=_dt("2025-10-10T00:00:00"),
    )
    insert_ah_kernel(
        mock_db_session,
        "imap_2025_282_2025_283_002.ah.bc",
        _dt("2025-10-09T21:00:00"),
        _dt("2025-10-10T18:00:00"),
        ingestion_date=_dt("2025-10-11T00:00:00"),
    )

    with instance_for_test() as instance:
        _reconcile(instance)
        assert set(instance.get_dynamic_partitions("pointing_attitude_partitions")) == {
            "pointingattitude_2025-09-30T14:00:00_to_2025-10-09T14:00:00"
        }
