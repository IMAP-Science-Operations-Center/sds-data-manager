"""IMAP job handler for managing dependencies and job submission."""

from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency_refactoring.dependency_new import (  # noqa: E501
    DependencyResolver,
)
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency_refactoring.utils import (  # noqa: E501
    UpstreamDependencyNode,
)


class IMAPJobHandler:
    """Handle IMAP job dependencies and submission."""

    def __init__(self, potential_job_node: UpstreamDependencyNode):
        """Initialize handler with job node and process dependencies.

        Parameters
        ----------
        potential_job_node : UpstreamDependencyNode
            The job node to process.
        """
        self.job_node = potential_job_node
        self.dependencies = self.get_dependencies(potential_job_node)
        self.is_duplicate_job = False
        self.job_dependencies_s3_filepath = None

        if self.dependencies is not None:
            self._calculate_crid()
            self._determine_job_version()
            self._create_dependencies_file()
            if not self.is_duplicate_job:
                job_success = self.submit_processing_job()
                if job_success:
                    self.clean_up()

    def get_dependencies(self, dependency_node: UpstreamDependencyNode):
        """Get the dependencies for the job using the DependencyResolver."""
        response = DependencyResolver().get_upstream_dependency(
            input_upstream_node=dependency_node
        )
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
        # 3. Add some hash for container image version.

    def _determine_job_version(self):
        """Determine job version for a potential job."""
        # TODO: what we have in determine_job_version
        # but refactor little bit but
        # keep same logic.
        pass

    def _create_dependencies_file(self):
        """Create and upload a dependency json file to S3 for the job.

        This file is a json file containting serialized output of upstream
        dependencies and information needed for IMAP job command line input (CLI).
        """
        # TODO: Remove information not needed for IMAP CLI input from
        # self.potential_job_node
        # cli_input = self.potential_job_node

        # upstream_dependency_content = self.dependencies.serialize()
        # TODO: write to dependency json file and upload to s3.
        upload_success = True
        if upload_success:
            # Save dependency file path to use for job submission step.
            self.job_dependencies_s3_filepath = (
                "s3://bucket/path/to/dependency_file.json"
            )
        else:
            self.is_duplicate_job = True
        # If CRID is calculated and dependency json file exists in s3,
        # then it means this is duplicate job submission.
        # NOTE: Do we want to give option to submit duplicate by human intervention?
        # If so, we need to add support for that.

    def submit_processing_job(self):
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
