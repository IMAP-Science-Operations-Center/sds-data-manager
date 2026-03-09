from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency_refactor import (
    DependencyResolver,
)
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.abstractions import (
    DependencyNode,
    UpstreamDependencyNode,
)


class IMAPJobHandler:

    def __init__(self, dependency_node: UpstreamDependencyNode):
        """Base class for handling managing dependencies call and job kickoff."""
        self.dependency_node = dependency_node
        self.dependencies = self.get_dependencies(dependency_node)
        self.dependency_s3_path = None
        if self.dependencies is not None:
            job_success = self.submit_job()
            if job_success:
                self.clean_up()

    def get_dependencies(self, dependency_node: UpstreamDependencyNode):
        """Get the dependencies for the job using the DependencyResolver."""
        response = DependencyResolver().upstream_discovery(dependency_node)
        if response["status"] == 200:
            return response["data"]
        else:
            return None

    def _determine_job_version(self):
        """Determining job version will be same for all trigger event types.

        Types of job kicked off are science or spacecraft products.
        All the trigger event types are only used for how we determine the date
        range and dependencies. Once we have those, the rest of the steps are same.
        """
        current_job_to_kickoff = self.dependency_node
        # determine the version of the job to run based self.dependency_node and
        # information available in the database.
        # keep what we have in determine_job_version but refactor little bit but
        # keep same logic.
        pass

    def _create_dependencies_file(self):
        """Calculate the CRID and create dependencies file for the job to submit.
        
        This is done same for all job types.
        """
        # Remove information not needed for CLI from self.dependency_node
        cli_input = self.dependency_node
        dependency_file_content = self.dependencies.serialize()
        # calculate the CRID for this job from serialized output and write to
        # dependency file.
        # Then update self.dependency_s3_path with the s3 path of the dependency file.
        pass

    def submit_job(self):
        """Submit job with input parameters and dependency information.

        This is done same for all job types.
        """
        self._determine_job_version()
        self._create_dependencies_file()
        # Finally, in this function, submit job to batch job with CLI
        # input of self.dependency_s3_path
        pass

    def clean_up(self):
        # clean up any resources or temporary files used during the job.
        # Eg. right now, we clean up SQS queue if job is submitted successfully.
        pass


# Batch Starter Lambda
def lambda_handler(event, context):
    # Determine what trigger event type
    # Get (source, data_type, product_name) to query for downstream
    # Get downstream nodes
    # For each downstream node
    #   calculate date range list based combination of trigger event type and
    #   the current downstream's processing job type. Eg. 
    #       - ancillary event + daily downstream job → list of (start_date, end_date) for each day
    #       - reprocessing event + daily downstream job → list of (start_date, end_date) for each day in range
    #       - cadence event + cadence downstream job → single (start_date, end_date) for cadence range
    #       - reprocessing event + cadence downstream job → one or more (start_date, end_date) ranges
    #       - science (HI DE) event + L1B goodtimes downstream job → list for 7 nearest repoint files
    #       - science (ENA/GLOWS) event + pointing downstream job → list for date ranges derived using repoint id of input file
    #       - reprocessing event + pointing downstream job → list of date ranges derived for each pointing in date range
    #       etc.
    #
    #   For each calculated date range, let IMAPJobHandler do these steps in batch_starter_refactor.py:
    #       - Query dependencies
    #       - Determine job version
    #       - Create dependency file
    #       - Submit job
    pass
