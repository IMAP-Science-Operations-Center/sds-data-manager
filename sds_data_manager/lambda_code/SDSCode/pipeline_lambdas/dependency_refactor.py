"""Dependency lambda details

Inputs:
    Source
    Data_type
    Product_name
    Start_time: yyyymmddhhmmss
    End_time: yyyymmddhhmmss

Responsibilities:
    Lookup upstream dependencies
    Lookup downstream dependencies
    Find all relavant files for upstream dependencies
    Determining if it's a complete list.
        Scenarios causing imcompleteness:
            1. Missing files in the database.
            2. Due to event of anamoly. Eg. LOI or TCM or solar wind
            3. Due to repoint data delay or downlink delay.
            4. If required dependencies missing or job IN PROGRESS.

Functionality:
Look for all dependencies for given inputs and return all the available
dependencies irrespective of if we have all the dependencies or not.
Let user or caller code decide about it.

Return type: dict
Containing all available data for all identified upstream dependents within input date range. Eg.
{
  status: 200,
  message: "success or missing but we still return all dependencies we found",
  data: {
    (Upstream details node): {
        missing_files: [(source, data_type, descriptor)],
        found_files: [list of files],
        }
    ...
}

"""

from sds_data_manager.lambda_code.SDSCode.database import db
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.abstractions import (
    DependencyConfig,
    DependencyNode,
    UpstreamDependencyNode,
)

class DependencyResolver():
    dependency_config = DependencyConfig()

    def downstream_discovery(self, downstream_inputs: DependencyNode):

        return DependencyNode.serialize()

    def upstream_discovery(self, upstream_inputs: UpstreamDependencyNode):

	    # look up upstream dependency based on input parameters
        # and through db queries
        return {
                "status": 200, # if found otherwise other status code
                "message": "", # if found otherwise message of which upstream are missing
                "data": ["list of files"] # or empty list or partial list of files found
        }
    def get_science_files(self):
        pass

    def get_ancillary_files(self):
        pass

    def get_spin_files(self):
        pass

    def get_spice_files(self):
        pass

    def get_repoint_files(self):
        pass


def handler(dependency_node: DependencyNode):
    resolver = DependencyResolver()
    return resolver.upstream_discovery(dependency_node)
