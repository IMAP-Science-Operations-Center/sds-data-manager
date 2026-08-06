"""Override behavior for MAG processing."""

import datetime
import re

from dagster import DagsterRunStatus, RunsFilter
from imap_data_access import processing_input

from sds_data_manager.orchestration import imap_job, types
from sds_data_manager.orchestration.dagster_utilities import (
    parse_dates_from_partition_key,
)
from sds_data_manager.orchestration.job_handler_registry import JobBuilderRegistry

# run_job retries with RetryRequested(max_retries=10) when
# _check_for_running_dependencies returns True. On the last allowed attempt
# MagL1CJob stops waiting for the previous day's L1C, so the added wait can
# delay a run but never fail one.
FINAL_RETRY_NUMBER = 10


@JobBuilderRegistry.register("mag", "l1c", "norm-mago")
@JobBuilderRegistry.register("mag", "l1c", "norm-magi")
class MagL1CJob(imap_job.IMAPJobHandler):
    """Deliver the previous day's L1C to MAG L1C processing.

    MAG L1C continues the previous day's L1C timeline across the day boundary
    when the current day opens with a gap (imap_processing#3323). The previous
    day's L1C is this job's own output product, so it is deliberately not
    declared as an input in imap_mag_dependencies.yaml: a declared self-input
    would put a cycle in the Dagster asset graph, and the generic input query
    would feed a reprocessing run its own earlier output. The file is fetched
    here at input-collection time instead.

    Downlinks arrive in multi-day batches, so day N's job can start before
    day N-1's L1C has been built. _check_for_running_dependencies therefore
    treats a pending previous-day L1C as a running dependency: the job
    retries while day N-1's L1C job is in flight or expected (day N-1 has
    L1B data but no L1C and no finished L1C run), proceeds immediately when
    day N-1 provably has nothing to deliver, and proceeds without the
    previous day on the last retry. A reprocessed day can still inherit the
    previous generation's L1C when the previous day's rerun is not in flight
    at the time (acceptable per MAG when that earlier version was complete).
    """

    def _check_for_running_dependencies(self, context):
        """Also treat a pending previous-day L1C as a running dependency."""
        if super()._check_for_running_dependencies(context):
            return True
        if context.retry_number >= FINAL_RETRY_NUMBER:
            context.log.info(
                "Out of retries waiting for the previous day's L1C; "
                "proceeding without it."
            )
            return False
        return self._previous_day_l1c_pending(context)

    def _previous_day_l1c_pending(self, context):
        """Return True while the previous day's L1C is in flight or expected."""
        target_start, _ = parse_dates_from_partition_key(context.partition_key)
        # One day's partition ends at the exact midnight the next day's
        # begins, and _get_overlapping_target_partitions matches inclusively,
        # so trim one second from both ends of the previous day: the window
        # then touches only day N-1's partition, not day N-2's (which ends at
        # day N-1's midnight) or this run's own (which starts at target_start).
        previous_day_keys = set(
            self._get_overlapping_target_partitions(
                None,
                target_start - datetime.timedelta(days=1, seconds=-1),
                target_start - datetime.timedelta(seconds=1),
                context.instance,
            )
        )
        if not previous_day_keys:
            return False

        own_asset = self.job_config.outputs[0].to_dagster_asset()
        in_flight_runs = context.instance.get_runs(
            filters=RunsFilter(
                statuses=[
                    DagsterRunStatus.QUEUED,
                    DagsterRunStatus.STARTING,
                    DagsterRunStatus.STARTED,
                ]
            )
        )
        for run in in_flight_runs:
            if run.tags.get("dagster/partition") not in previous_day_keys:
                continue
            # Sensor-requested runs carry this job's name; reprocessing
            # backfill runs carry the asset selection instead.
            if run.job_name == self.dagster_job_name or own_asset in (
                run.asset_selection or ()
            ):
                context.log.info(
                    f"Previous day's L1C job is in flight ({run.run_id}); waiting."
                )
                return True

        materialized_l1c = set(context.instance.get_materialized_partitions(own_asset))
        if materialized_l1c & previous_day_keys:
            return False  # a previous-day L1C exists; it will be delivered

        if not any(
            previous_day_keys
            & set(context.instance.get_materialized_partitions(dep.to_dagster_asset()))
            for dep in self.job_config.science_inputs
        ):
            return False  # the previous day has no L1B data: nothing to wait for

        for key in previous_day_keys:
            finished_runs = context.instance.get_runs(
                filters=RunsFilter(
                    statuses=[
                        DagsterRunStatus.SUCCESS,
                        DagsterRunStatus.FAILURE,
                        DagsterRunStatus.CANCELED,
                    ],
                    tags={"dagster/partition": key},
                )
            )
            for run in finished_runs:
                # The partition tag is shared by every daily job, so match
                # this job the same way as the in-flight check above.
                if run.job_name == self.dagster_job_name or own_asset in (
                    run.asset_selection or ()
                ):
                    return False  # its job already ran and skipped or failed

        context.log.info(
            "The previous day has L1B data but no L1C yet; waiting for its job."
        )
        return True

    def get_science_files_inputs(self, context, target_start, target_end):
        """Return the base science inputs plus the previous day's L1C, if any."""
        science_processing_inputs = super().get_science_files_inputs(
            context, target_start, target_end
        )

        previous_day_l1c = types.DependencyNode(
            source="mag",
            data_type="l1c",
            descriptor=self.job_config.descriptor,
            required=False,
            trigger_job=False,
        )
        # The query window ends at target_start so that the strict overlap
        # check in get_all_files_in_time_range cannot match the current day's
        # own partition, which starts exactly at target_start. The job never
        # receives its own output as input.
        metadata_list = previous_day_l1c.get_all_files_in_time_range(
            context, target_start - datetime.timedelta(days=1), target_start
        )

        if not metadata_list:
            context.log.info(
                "No previous day L1C found; MAG L1C processes this day alone."
            )
            return science_processing_inputs

        # get_all_files_in_time_range returns the latest materialization of
        # each overlapping partition, and this one-day window can only overlap
        # the previous day's partition, so there is exactly one entry. Science
        # materializations carry a single file in file_names (find_outputs),
        # wrapped in a Dagster MetadataValue.
        previous_day_file = metadata_list[0]["file_names"].value[0]

        # Apply the same version-renaming strategy as
        # IMAPJobHandler.get_science_files_inputs so the previous day's file is
        # named consistently with the base science inputs.
        pattern = re.compile(r"v(\d{3})\.(cdf|pkts)$")
        renamed_previous_day_file = pattern.sub(r"v001.0\1.\2", previous_day_file)
        context.log.info(
            f"MAG L1C adding the previous day's L1C: {renamed_previous_day_file}"
        )
        science_processing_inputs.append(
            processing_input.ScienceInput(renamed_previous_day_file)
        )

        return science_processing_inputs
