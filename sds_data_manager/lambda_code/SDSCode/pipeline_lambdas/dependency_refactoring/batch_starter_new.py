"""IMAP job handler for managing dependencies and job submission."""

from sds_data_manager.lambda_code.SDSCode.database import database as db

from ..batch_starter import dependency_hash, upload_dependency_file
from .dependency_new import DependencyResolver
from .utils import UpstreamDependencyNode


class IMAPJobHandler:
    """Handle IMAP job dependencies and submission."""

    def __init__(self, potential_job_node: UpstreamDependencyNode):
        """Initialize handler with job node and process dependencies.

        Parameters
        ----------
        potential_job_node : UpstreamDependencyNode
            The job node to process.
        """
        self.process_job(potential_job_node)

    def process_job(self, potential_job_node: UpstreamDependencyNode):
        """Process the job by resolving dependencies and submitting to batch."""
        self.dependencies = self.get_dependencies(potential_job_node)

        if self.dependencies is not None:
            self._calculate_crid()
            self._determine_job_version()
            job_dependencies_s3_filepath = self._create_dependencies_file()
            dependency_serialized_hash = dependency_hash(self.dependencies.serialize())
            is_duplicate_job = self.is_duplicate_job(
                potential_job_node, dependency_serialized_hash
            )
            if not is_duplicate_job:
                upload_response = upload_dependency_file(
                    self.dependencies.serialize(), job_dependencies_s3_filepath
                )
                if upload_response["status"] != 200:
                    raise Exception("Failed to upload dependency file to S3.")

                job_submit_succeed = self.submit_processing_job(
                    job_dependencies_s3_filepath
                )
                if job_submit_succeed:
                    self.clean_up()

    def get_dependencies(self, dependency_node: UpstreamDependencyNode):
        """Get the dependencies for the job using the DependencyResolver."""
        with db.Session() as session:
            response = DependencyResolver().get_upstream_dependency(
                session=session, input_upstream_node=dependency_node
            )
            # If dependency status is 200, then it means that we have complete set of
            # dependencies needed.
            if response["status"] == 200:
                return response["data"]

        return None

    def _calculate_crid(self):
        """Calculate CRID for a potential job.

        Return:
        ------
        str
            The calculated CRID for the potential job.
        """
        # TODO: Update CRID calculation logic or decide if it should be
        # its own class.
        # 1. Review and keep logic from current CRID logic
        # 2. Refactor current CRID logic into this funciton
        return ""

    def is_duplicate_job(
        self,
        potential_job_node: UpstreamDependencyNode,
        serialized_dependency_hash: str,
    ) -> bool:
        """Determine if the job is a duplicate.

        Requirements for duplicate job determination:
            1. Must be unique dependency serialized hash AND
            2. AWS ECR container image digest hash must be unique AND
            3. Potential job node's must be unique AND
            4. Job status must be either INPROGRESS or SUCCEEDED.
        """
        # 1. Get AWS ECR container image digest hash, container_image_digest.
        #    This should unique.
        # 2. Now query DB with these inputs and we will know if a job is duplicate.
        #   max_version_record = (
        #     session.query(models.ProcessingJob)
        #     .filter(table.instrument == potential_job_node.instrument,
        #             table.data_level == potential_job_node.data_level,
        #             table.descriptor == potential_job_node.descriptor,
        #             table.start_date == potential_job_node.start_date,
        #             table.repoint == potential_job_node.repoint,
        #             table.dependency_hash == serialized_dependency_hash,
        #             table.contianer_image_digest == container_image_digest,
        #             table.status.in_(
        #                 [models.Status.INPROGRESS.value,
        #                   models.Status.SUCCEEDED.value]
        #             )
        #             )
        #     .order_by(models.ProcessingJob.version.desc())
        #     .first()
        # )
        # 3. If return exists, it's a duplicate job and return True.

        return False

    def _determine_job_version(self):
        """Determine job version for a potential job."""
        # TODO: what we have in determine_job_version
        # but refactor little bit but
        # keep same logic.
        return "v001"

    def _create_dependencies_file(self):
        """Create and upload a dependency json file to S3 for the job.

        This file is a json file containting serialized output of upstream
        dependencies and information needed for IMAP job command line input (CLI).
        """
        # TODO: Remove information not needed for IMAP CLI input from
        # self.potential_job_node
        # cli_input = self.potential_job_node

        # TODO: convert start_date and end_date to string and format needed
        # for CLI input. Eg. "yyyymmdd"

        # upstream_dependency_content = self.dependencies.serialize()
        # TODO: write to dependency json file.
        dependency_file_path = "/some/path/dependency_file.json"
        return dependency_file_path

    def submit_processing_job(self, job_dependencies_s3_filepath: str):
        """Submit AWS batch processing job with dependencies and inputs.

        Return:
        ------
        bool
            True if job is submitted successfully, False otherwise.
        """
        # Finally, in this function, submit job to batch job with CLI
        # input of self.dependency_s3_path
        return True

    def clean_up(self):
        """Clean up resources or temporary files used during job processing."""
        # clean up any resources or temporary files used during the job.
        # Eg. right now, we clean up SQS queue if job is submitted successfully.
        pass


def lambda_handler(event, context):
    """Lambda handler for batch starter.

    Parameters
    ----------
    event : dict
        AWS Lambda event dict with trigger information.
    context : object
        AWS Lambda context object with runtime information.
    """
    # Determine trigger event type based on trigger file or event.
    # Get (source, data_type, product_name) to query for potential job
    # Get potential job nodes
    # For each potential job node calculate date range list based on:
    #   - trigger event type
    #   - current potential job's processing job type
    # Examples:
    #   - ancillary + daily job → (start_date, end_date) for each day
    #   - reprocessing + daily job → (start_date, end_date) per day in range
    #   - cadence + cadence job → single (start_date, end_date) for cadence
    #   - reprocessing + cadence job → multiple (start_date, end_date) ranges
    #   - science (HI DE) + L1B goodtimes → 7 nearest repoint files
    #   - science (ENA/GLOWS) + pointing → date ranges from repoint id
    #   - reprocessing + pointing → date ranges per pointing in range
    #
    # For each calculated date range, use IMAPJobHandler to:
    #   - Query dependencies
    #   - Determine job version
    #   - Create dependency file
    #   - Submit AWS batch processing job
    pass
