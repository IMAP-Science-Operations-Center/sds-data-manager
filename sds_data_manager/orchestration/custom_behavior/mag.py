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
    when the current day opens with a gap (imap_processing#2925, implemented
    in imap_processing#3323). The previous day's L1C is this job's own output
    product, so it is deliberately not declared as an input in
    imap_mag_dependencies.yaml: a declared self-input would put a cycle in
    the Dagster asset graph, and the generic input query would feed a
    reprocessing run its own earlier output. The file is fetched here at
    input-collection time instead.

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
        previous_day_keys = self._get_overlapping_target_partitions(
            None,
            target_start - datetime.timedelta(days=1) + datetime.timedelta(seconds=1),
            target_start - datetime.timedelta(seconds=1),
            context.instance,
        )
        if not previous_day_keys:
            return False

        in_flight_runs = context.instance.get_runs(
            filters=RunsFilter(
                statuses=[
                    DagsterRunStatus.QUEUED,
                    DagsterRunStatus.STARTING,
                    DagsterRunStatus.STARTED,
                ],
                tags={"dagster/partition": previous_day_keys},
            )
        )
        for run in in_flight_runs:
            if self._is_own_job_run(run):
                context.log.info(
                    f"Previous day's L1C job is in flight ({run.run_id}); waiting."
                )
                return True

        # Nothing in flight does not mean nothing is coming: the kickoff
        # sensor requests day N-1's run only on a tick after its L1B lands.
        # The materializations decide whether an L1C is still expected.
        own_asset = self.job_config.outputs[0].to_dagster_asset()
        materialized_l1c = context.instance.get_materialized_partitions(own_asset)
        if set(materialized_l1c).intersection(previous_day_keys):
            return False  # a previous-day L1C exists; it will be delivered

        if not any(
            set(
                context.instance.get_materialized_partitions(dep.to_dagster_asset())
            ).intersection(previous_day_keys)
            for dep in self.job_config.science_inputs
        ):
            return False  # the previous day has no L1B data: nothing to wait for

        # Day N-1 has L1B but no L1C. If its job already finished (failed, or
        # skipped as a SUCCESS run that only logged an AssetObservation), no
        # L1C is coming without intervention, so waiting only delays this run.
        finished_runs = context.instance.get_runs(
            filters=RunsFilter(
                statuses=[
                    DagsterRunStatus.SUCCESS,
                    DagsterRunStatus.FAILURE,
                    DagsterRunStatus.CANCELED,
                ],
                tags={"dagster/partition": previous_day_keys},
            )
        )
        if any(self._is_own_job_run(run) for run in finished_runs):
            return False  # its job already ran and skipped or failed

        # Reached when day N-1's L1B has landed but the sensor has not yet
        # requested its L1C run, which is the case this wait exists for.
        context.log.info(
            "The previous day has L1B data but no L1C yet; waiting for its job."
        )
        return True

    def _is_own_job_run(self, run):
        """Return True if the run is a run of this job.

        The dagster/partition tag is shared by every daily job, so the tag
        alone cannot identify this job's runs. Sensor runs carry this job's
        name. Reprocessing runs come from a backfill (reprocessing.py) and
        carry Dagster's implicit asset job name ("__ASSET_JOB"), so they are
        matched by asset selection, which RunsFilter cannot filter on.
        """
        own_asset = self.job_config.outputs[0].to_dagster_asset()
        return run.job_name == self.dagster_job_name or own_asset in (
            run.asset_selection or ()
        )

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
