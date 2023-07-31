"""Module with helper functions for creating standard sets of stacks"""
# Installed
from aws_cdk import App, Environment

# Local
from sds_data_manager.stacks import (
    api_gateway_stack,
    domain_stack,
    opensearch_stack,
    sds_data_manager_stack,
    step_function_stack,
)


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

    data_manager = sds_data_manager_stack.SdsDataManager(
        scope, f"SdsDataManager-{sds_id}", sds_id, open_search, env=env
    )

    domain = domain_stack.Domain(
        scope,
        f"DomainStack-{sds_id}",
        sds_id,
        env=env,
        use_custom_domain=use_custom_domain,
    )

    api_gateway_stack.ApiGateway(
        scope,
        f"ApiGateway-{sds_id}",
        sds_id,
        data_manager.lambda_functions,
        env=env,
        hosted_zone=domain.hosted_zone,
        certificate=domain.certificate,
        use_custom_domain=use_custom_domain,
    )

    step_function_stack.ProcessingStepFunctionStack(
        scope,
        f"ProcessingStepFunctionStack-{sds_id}",
        sds_id,
        dynamodb_table_name="imap-data-watcher-tc-dev",
        env=env,
    )
