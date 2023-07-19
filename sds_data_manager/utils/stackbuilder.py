"""Module with helper functions for creating standard sets of stacks"""
# Installed
from aws_cdk import App, Environment

# Local
from sds_data_manager.stacks import opensearch_stack, sds_data_manager_stack


def build_sds(
    scope: App, env: Environment, sds_id: str, use_custom_domain: bool = False
):
    """Builds the entire SDS

    Parameters
    ----------
    scope : App
    env : Environment
        Account and region
    sds_id : str
        Name suffix for stack
    use_custom_domain : bool, Optional
        Build API Gateway using custom domain
    """
    open_search = opensearch_stack.OpenSearch(
        scope, f"OpenSearch-{sds_id}", sds_id, env=env
    )

    sds_data_manager_stack.SdsDataManager(
        scope, f"SdsDataManager-{sds_id}", sds_id, open_search, env=env
    )
