from sds_data_manager.orchestration import imap_job, types
from dagster import AssetExecutionContext
from sds_data_manager.lambda_code.SDSCode.database import database as db
from sds_data_manager.lambda_code.SDSCode.database import models
import numpy as np

HI_GOODTIMES_NUM_NEAREST_REPOINTS = 8

class HiGoodtimesJob(imap_job.IMAPJobHandler):

    def get_science_file_inputs(self, context, target_start, target_end):
        with db.Session() as session:
            parts = context.partition_key.split("_")
            if "repoint" in parts[0]:
                target_pointing_number = int(parts[0][7:])
            science_files = super().get_science_file_inputs(context, target_start, target_end)

            for input in self.job_config.science_inputs:
                if "-de" in input.descriptor:
                    repoint_list = self.get_n_nearest_repoints(context, session, input, target_pointing_number)
                    metadata_list = input.get_all_files_by_repoint_numbers(context, repoint_list)

            if metadata_list is None:
                raise imap_job.MissingDependencies(f"Hi Goodtimes: skipping repoint {target_pointing_number} due to INPROGRESS jobs")
        
            for metadata in metadata_list:
                if "file_names" in metadata:
                    # Dagster wraps metadata in a MetadataValue object, so we call .value
                    file_names = metadata["file_names"].value
                    # Handle both single strings and lists of files safely
                    if isinstance(file_names, str):
                        file_names = [file_names]
                    if file_names:
                        context.log.info(f"The file names of the matching partition: {file_names}")
                    science_files.extend(file_names)
        
        num_future = np.sum(repoint_list > target_pointing_number)
        min_future_repoints = HI_GOODTIMES_NUM_NEAREST_REPOINTS // 2
        if num_future < min_future_repoints:
            required_future_pointing = target_pointing_number + HI_GOODTIMES_NUM_NEAREST_REPOINTS
            if not self._check_pointing_exists(session, required_future_pointing):
                context.log.info(
                    f"Hi Goodtimes: skipping repoint {target_pointing_number} - pointing "
                    f"{required_future_pointing} does not exist yet"
                )
                return None

        context.log.info(f"Hi Goodtimes adding L1B DE files: {science_files}")

        return science_files
            
    def get_n_nearest_repoints(
            self,
            context,
            session: db.Session,
            dependency: types.DependencyNode,
            repoint: int) -> list | None:
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
        inprogress_repoints = np.array(self._get_inprogress_repoints(session, dependency))
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
        nearest_repoints = other_repoints[sort_indices][:HI_GOODTIMES_NUM_NEAREST_REPOINTS]

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
            self,
            context: AssetExecutionContext,
            dependency: types.DependencyNode
        ) -> list[int]:
        """Query distinct repoint values that exist for a dependency.

        Parameters
        ----------
        session : db.Session
            Database session.
        dependency : DependencyNode
            A dataclass containing the source, data_type, descriptor.

        Returns
        -------
        list[int]
            Sorted list of repoint numbers that have data.
        """
        repoints = []
        materialized_partitions = context.instance.get_materialized_partitions(dependency.to_dagster_asset())
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
    