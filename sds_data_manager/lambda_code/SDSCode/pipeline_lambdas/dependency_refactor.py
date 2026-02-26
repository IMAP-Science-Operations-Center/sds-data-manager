"""Dependency lambda details

Inputs:
    Source
    Data_type
    Product_name
    Start_time: yyyymmddhhmmss
    End_time: yyyymmddhhmmss

Responsibilities:
    Determine upstream dependencies
    Determine downstream dependencies
    Find all relavant files for upstream dependencies
    Determining if it's a complete list.

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
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.utils import (
    DependencyConfig,
    DependencyNode,
)


class DependencyResolver:
    def __init__(self):
        # DependencyConfig will need to handle reading in all the instrument specific files
        self.config = DependencyConfig()

    def resolve_downstream(self):
        # look for all downstream dependency node
        pass

    def resolve_upstream(
        self,
        query: DependencyNode,
    ) -> dict[DependencyNode, list[str]]:
        """Returns all available upstream dependency files.
        Does NOT enforce coverage or completeness.
        """
        upstream_nodes = self.config.get_dependencies(
            (query.source, query.data_type, query.product_name),
        )

        results: dict[DependencyNode, list[str]] = {}

        with db.Session() as session:
            for dep in upstream_nodes:
                node = DependencyNode(
                    dep["data_source"],
                    dep["data_type"],
                    dep["descriptor"],
                    query.start_date,
                    query.end_date,
                )

                if dep["data_type"] == "science":
                    # this self.get_science_files() and other functions break down current
                    # get_files() into more specific functions for each data type.
                    records = self.get_science_files()
                # so on with other cases.

                # By this step if we would know if we have all dependencies.
                # Based on this, add information to return object.
                filenames = [record.file_path for record in records] if records else []

                results[node] = filenames

        return results

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
    return resolver.resolve_upstream(dependency_node)
