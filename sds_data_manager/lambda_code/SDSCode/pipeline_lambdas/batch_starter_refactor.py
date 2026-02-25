from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.dependency_refactor import (
    DependencyResolver,
)
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.utils import (
    DependencyNode,
)


class IMAPJobHandler:
    def __init__(self, event: dict, reprocessing: bool = False):
        # dependency on event type, extract
        # parameters from event and assign to the node.
        self.node = DependencyNode(
            source=event.get("source", ""),
            data_type=event.get("data_type", ""),
            product_name=event.get("product_name", ""),
            start_date=event.get("start_date", None),
            end_date=event.get("end_date", None),
        )
        self.node.reprocessing = reprocessing
        self.get_dependencies = DependencyResolver()

    def calculate_date_range(self):
        pass

    def determine_job_version(self):
        # determine the version of the job to run based information available
        # in the database.
        # version is for all science files that we want produce.
        # keep what we have in determine_job_version but refactor little bit but
        # keep same logic.
        pass

    def calculate_crid(self):
        # calculate the CRID for this job from serialized output and write to
        # dependency file.
        pass

    def submit_job(self):
        # submit the job to the job queue with the calculated parameters and CRID
        # TODO: figure out where filter dependencies should go.
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
