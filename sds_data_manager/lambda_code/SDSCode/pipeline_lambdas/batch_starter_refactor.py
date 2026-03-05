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
        response = DependencyResolver().resolve_upstream(dependency_node)
        if response["status"] == 200:
            return response["data"]
        else:
            return None

    def _determine_job_version(self):
        """Determining job version will be same for all event types.

        Types of job kicked off are science or spacecraft products.
        All the event types are only used for how we determine the date
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
    # Determine what event source type
    # Get (source, data_type, product_name) to query for downstream
    # Get downstream nodes
    # For each downstream node
    #   calculate date range based combination of event source type and
    #   the current downstream's processing job type. Eg. 
    #       1. if ancillary event and daily science file 
    #           downstream job, then calculate list of (start_date, end_date)
    #           for each daily date from the ancillary date range.
    #       2. if reprocessing event and daily science downstream job, then calculate list
    #           of (start_date, end_date) for each day in the reprocessing date
    #           range.
    #       3. if cadence event and cadence science downstream job, then calculate one
    #          (start_date, end_date) for the whole cadence range.
    #       4. if reprocess and cadence science downstream job, then calculate one
    #           or multiple (start_date, end_date) for given start and end dates input.
    #       5. If Hi DE event and L1B goodtimes downstream job, then calculate list of
    #          (start_date, end_date) for last 7 nearest repoint files to the HI L1B DE
    #          event's repoint id.
    #          NOTE: Then, call IMAPJobHandler and use that list to query for dependencies and
    #          submit jobs using for all goodtimes jobs that fall into those date ranges.
    #          Dependency lambda will query for (-3p, 3p) when looking up dependencies.
    #       6. If ENA or GLOWS science file event and pointing science downstream job,
    #          then calculate list of (start_date, end_date) for the repoint
    #          id of the input file.
    #       5. if reprocessing event and pointing science downstream job, then calculate list
    #           of (start_date, end_date) for each pointing in the reprocessing date
    #           range.
    #       etc.
    #   call IMAPJobHandler to do rest of the work of getting dependencies,
    #   determining job version, creating dependency file and submitting job.
    #   This is same for all jobs being submitted, the only difference is the
    #   input parameters we pass to IMAPJobHandler which is based on the event
    #   source type and downstream job type.
    pass
