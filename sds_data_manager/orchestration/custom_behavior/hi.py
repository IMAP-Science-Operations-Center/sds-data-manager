"""Override behavior for HI processing."""

import re

import numpy as np
from dagster import AssetExecutionContext
from imap_data_access import processing_input

from sds_data_manager.lambda_code.SDSCode.database import database as db
from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.orchestration import imap_job, types
from sds_data_manager.orchestration.job_handler_registry import JobBuilderRegistry

HI_GOODTIMES_NUM_NEAREST_REPOINTS = 8


@JobBuilderRegistry.register("hi", "l1b", "45sensor-goodtimes")
@JobBuilderRegistry.register("hi", "l1b", "90sensor-goodtimes")
class HiGoodtimesJob(imap_job.IMAPJobHandler):
    """Overriding parts of the Hi processing pipeline."""

    # Override this function from IMAPJobHandler
    def get_science_files_inputs(self, context, target_start, target_end):
        """Override the behavior of IMAPJobHandler.get_science_files_inputs."""
        parts = context.partition_key.split("_")
        target_pointing_number = int(parts[0][7:])

        science_processing_inputs = super().get_science_files_inputs(
            context, target_start, target_end
        )

        with db.Session() as session:
            for input in self.job_config.science_inputs:
                if "-de" not in input.descriptor:
                    continue

                repoint_list = self.get_n_nearest_repoints(
                    context, session, input, target_pointing_number
                )
                # A neighboring repoint's L1B DE job is still INPROGRESS.
                # Wait for it to resolve rather than using a stale file.
                if repoint_list is None:
                    raise imap_job.MissingDependenciesError(
                        f"Hi Goodtimes: skipping repoint {target_pointing_number} "
                        "- a neighboring repoint's L1B DE job is in progress."
                    )

                num_future = np.sum(np.array(repoint_list) > target_pointing_number)
                min_future_repoints = HI_GOODTIMES_NUM_NEAREST_REPOINTS // 2
                if num_future < min_future_repoints:
                    # Fewer than half of our nearest window are future
                    # pointings. That's fine to proceed with if the pointing
                    # at the edge of our window already exists in
                    # PointingTable - its existence there without a
                    # corresponding Hi L1B DE means Hi simply has no data for
                    # it, not that we haven't waited long enough, so our
                    # nearest set won't change no matter how long we wait.
                    # But if that pointing doesn't exist yet, the picture is
                    # still incomplete: more pointings - and likely more Hi
                    # L1B DE files - will show up later and change our
                    # nearest set. Hold off and let the sensor retrigger this
                    # job once that data has filled in.
                    required_future_pointing = (
                        target_pointing_number + HI_GOODTIMES_NUM_NEAREST_REPOINTS
                    )
                    if not self._check_pointing_exists(
                        session, required_future_pointing
                    ):
                        raise imap_job.MissingDependenciesError(
                            f"Hi Goodtimes: skipping repoint "
                            f"{target_pointing_number} - pointing "
                            f"{required_future_pointing} does not exist yet, "
                            "waiting for more data to fill in."
                        )

                if not repoint_list:
                    continue

                metadata_list = input.get_all_files_by_repoint_numbers(
                    context, repoint_list
                )
                neighbor_files = []
                for metadata in metadata_list:
                    if "file_names" in metadata:
                        # Dagster wraps metadata in a MetadataValue object
                        file_names = metadata["file_names"].value
                        # Handle both single strings and lists of files safely
                        if isinstance(file_names, str):
                            file_names = [file_names]
                        neighbor_files.extend(file_names)

                if neighbor_files:
                    # Apply the same version-renaming strategy as
                    # IMAPJobHandler.get_science_files_inputs so neighboring
                    # files are named consistently with the base science
                    # inputs. This implementation will leave filenames with the
                    # new versioning convention unaltered.
                    pattern = re.compile(r"v(\d{3})\.cdf$")
                    renamed_neighbor_files = [
                        pattern.sub(r"v001.0\1.cdf", file) for file in neighbor_files
                    ]
                    context.log.info(
                        "Hi Goodtimes adding neighboring L1B DE files: "
                        f"{renamed_neighbor_files}"
                    )
                    science_processing_inputs.append(
                        processing_input.ScienceInput(
                            *list(set(renamed_neighbor_files))
                        )
                    )

        return science_processing_inputs

    def get_n_nearest_repoints(
        self,
        context,
        session: db.Session,
        dependency: types.DependencyNode,
        repoint: int,
    ) -> list | None:
        """Get N files nearest to a target repoint.

        Finds N files nearest by repoint number. Does NOT include the target
        repoint itself.

        Parameters
        ----------
        context : AssetExecutionContext
            The execution context when materializing this Asset in Dagster
        session : db.Session
            Database session.
        dependency : types.DependencyNode
            Dataclass containing source, data_type, descriptor.
        repoint : int
            Target repoint number.

        Returns
        -------
        dict or None
            Metadata records for N nearest files. Empty list if no neighbors
            exist. None if skip_if_inprogress=True and any of N nearest are
            INPROGRESS.
        """
        # Get available repoints from existing files
        available_repoints = np.array(self._get_available_repoints(context, dependency))

        # Also get inprogress repoints from running jobs
        inprogress_repoints = np.array(
            self._get_inprogress_repoints(session, dependency)
        )
        all_repoints = np.union1d(available_repoints, inprogress_repoints)

        # Verify target exists (in available files or inprogress jobs)
        if repoint not in all_repoints:
            context.log.info(f"Target repoint {repoint} not found for {dependency}")
            return []

        # Remove target, sort by distance then repoint, take N nearest
        other_repoints = all_repoints[all_repoints != repoint]
        if len(other_repoints) == 0:
            return []

        distances = np.abs(other_repoints - repoint)
        sort_indices = np.lexsort((other_repoints, distances))
        nearest_repoints = other_repoints[sort_indices][
            :HI_GOODTIMES_NUM_NEAREST_REPOINTS
        ]

        # Check if any of N nearest are inprogress
        if len(inprogress_repoints) > 0:
            inprogress_nearest = nearest_repoints[
                np.isin(nearest_repoints, inprogress_repoints)
            ]
            if len(inprogress_nearest) > 0:
                context.log.info(
                    f"Skipping: nearest repoints {inprogress_nearest.tolist()} "
                    f"have INPROGRESS jobs for {dependency}"
                )
                return None

        # Get actual records via get_files (handles versioning)
        nearest_repoints_list = nearest_repoints.tolist()

        return nearest_repoints_list

    def _get_available_repoints(
        self, context: AssetExecutionContext, dependency: types.DependencyNode
    ) -> list[int]:
        """Query distinct repoint values that exist for a dependency.

        Parameters
        ----------
        context : AssetExecutionContext
            The Dagster context class
        dependency : DependencyNode
            A dataclass containing the source, data_type, descriptor.

        Returns
        -------
        list[int]
            Sorted list of repoint numbers that have data.
        """
        repoints = []
        materialized_partitions = context.instance.get_materialized_partitions(
            dependency.to_dagster_asset()
        )
        for m in materialized_partitions:
            parts = m.split("_")
            if "repoint" in parts[0]:
                repoints.append(int(parts[0][7:]))
        return repoints

    def _get_inprogress_repoints(
        self,
        session: db.Session,
        dependency: dict,
    ) -> list[int]:
        """Query distinct repoint values that have INPROGRESS jobs.

        Parameters
        ----------
        session : db.Session
            Database session.
        dependency : DependencyNode
            Dataclass containing source, data_type, descriptor.

        Returns
        -------
        list[int]
            Sorted list of repoint numbers that have INPROGRESS jobs.
        """
        results = (
            session.query(models.ProcessingJob.repointing)
            .filter(
                models.ProcessingJob.instrument == dependency.source,
                models.ProcessingJob.data_level == dependency.data_type,
                models.ProcessingJob.descriptor == dependency.descriptor,
                models.ProcessingJob.status == models.Status.INPROGRESS,
                models.ProcessingJob.repointing.isnot(None),
            )
            .distinct()
            .order_by(models.ProcessingJob.repointing)
            .all()
        )
        return [rp[0] for rp in results]

    def _check_pointing_exists(self, session: db.Session, repoint: int) -> bool:
        """Check if a pointing exists in the pointing table.

        Parameters
        ----------
        session : db.Session
            Database session.
        repoint : int
            The repoint/pointing ID to check.

        Returns
        -------
        bool
            True if the pointing exists, False otherwise.
        """
        pointing_record = (
            session.query(models.PointingTable)
            .filter(models.PointingTable.pointing_id == repoint)
            .first()
        )
        return pointing_record is not None
