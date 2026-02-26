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
        self.dependencies = self.get_dependencies()
        if self.dependencies is not None:
            job_success = self.submit_job()
            if job_success:
                self.clean_up()

    def get_dependencies(self):
        """Get the dependencies for the job using the DependencyResolver."""
        response = DependencyResolver(self.dependency_node).resolve_upstream()
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
        # determine the version of the job to run based information available
        # in the database.
        # keep what we have in determine_job_version but refactor little bit but
        # keep same logic.
        pass

    def _create_dependencies_file(self):
        """Calculate the CRID and create dependencies file for the job to submit.
        
        This is done same for all job types.
        """
        # calculate the CRID for this job from serialized output and write to
        # dependency file.
        pass

    def submit_job(self, dependencies_filename):
        """Submit job with input parameters and dependency information.
        
        This is done same for all job types.
        """
        self._determine_job_version()
        self._create_dependencies_file()
        # Finally, in this function, submit job to batch job.
        # TODO: figure out if filter dependencies is needed still.
        pass

    def clean_up(self):
        # clean up any resources or temporary files used during the job
        pass


def lambda_handler(event, context):
    # Determine what event source type
    # Get (source, data_type, product_name) to query for downstream
    # Get downstream nodes
    # For each downstream node
    #   calculate date range based combination of event source type and
    #   the current downstream's processing job type. Eg. 
    #       1. if ancillary event and daily
    #           science file job, then calculate list of (start_date, end_date)
    #           for each day in the ancillary date range.
    #       2. if reprocessing event and daily science job, then calculate list
    #           of (start_date, end_date) for each day in the reprocessing date
    #           range.
    #       3. if cadence event and cadence science job, then calculate one
    #          (start_date, end_date) for the whole cadence range.
    #       4. if ENA or GLOWS science file event and pointing science job,
    #          then calculate list of (start_date, end_date) for the repoint
    #          id of the input file.
    #       5. if reprocessing event and poiting science job, then calculate list
    #           of (start_date, end_date) for each pointing in the reprocessing date
    #           range.
    #       etc.
    #   call IMAPJobHandler to do rest of the work of getting dependencies,
    #   determining job version, creating dependency file and submitting job.
    pass
