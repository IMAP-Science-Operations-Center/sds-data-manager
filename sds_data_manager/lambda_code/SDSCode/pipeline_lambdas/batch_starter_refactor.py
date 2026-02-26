from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency_refactor import (
    DependencyResolver,
)
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.utils import (
    DependencyNode,
)


class IMAPJobHandler:
    source: str
    data_type: str
    product_name: str
    start_date: str
    end_date: str

    def __init__(self, event: dict, reprocessing: bool = False):
        """Base class for handling IMAP events and managing dependencies and job kickoff.

        Parameters:
        event (dict): The input event containing necessary information to process the job.
        reprocessing (bool): Flag indicating if this is a reprocessing job. Defaults to False
        """
        
        self.dependency_node.reprocessing = reprocessing

    def calculate_date_range(self):
        """Calculate the date range for the job based on the event type.
        
        Overwritten by the inheriting class."""
        pass

    def get_dependencies(self):
        """Get the dependencies for the job using the DependencyResolver.

        This function will be same for all event types but the input to
        dependency resolver will be derived differently based on event type.
        """
        pass

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

    def submit_job(self):
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


class IMAPScienceEventHandler(IMAPJobHandler):
    file_object: str

    def __init__(self, event: dict, reprocessing: bool = False):
        """We expect ditionary with s3 file key"""
        super().__init__(event)
        # create objec of imap file validator and extract parameters from s3 file key
        #
        pass

    def calculate_date_range(self):
        # if science file,
        #   if ena and glows:
        #      start_date = end_date = date range of pointing
        #  else:
        #     start_date = end_date = date of file
        pass


class IMAPPointingAttitudeEventHandler(IMAPJobHandler):
    file_object: str

    def __init__(self, event: dict, reprocessing: bool = False):
        """We expect ditionary with s3 file key"""
        super().__init__(event)
        # create objec of imap file validator and extract parameters from s3 file key
        #
        pass

    def calculate_date_range(self):
        # if pointing or attitude file,
        #   start_date = end_date = date of file
        pass


class IMAPCadenceEventHandler(IMAPJobHandler):
    event_type: str

    def __init__(self, event: dict, reprocessing: bool = False):
        """We expect dictionary with event type and parameters"""
        super().__init__(event, reprocessing)
        # extract parameters from event and assign to class variables
        pass

    def calculate_date_range(self):
        # if ancillary event,
        #   get start date from the filename paramters
        #   get end date from filename parameter if exists. otherwise,
        #   use end_date as now.
        pass


class IMAPReprocessingEventHandler(IMAPJobHandler):
    def __init__(self, event: dict):
        super().__init__(event, reprocessing=True)
        # extract parameters from event and assign to class variables
        pass

    def calculate_date_range(self):
        # for reprocessing events, we want to calculate the date range based on the repointing parameter.
        # if repointing parameter is not provided, we can default to 1 day before and after the start date.
        pass
