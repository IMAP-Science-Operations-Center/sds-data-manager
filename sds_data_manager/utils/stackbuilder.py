"""Module with helper functions for creating standard sets of stacks"""
# Installed
from aws_cdk import (
    App,
    Environment
)
# Local
from sds_data_manager.stacks import (
    api_gateway_stack,
    domain_stack,
    lambda_stack,
    opensearch_stack,
    sds_data_manager_stack
)


def build_sdc(scope: App, env: Environment,
              sds_id: str):
    """Builds the entire SDC

    Parameters
    ----------
    scope : App
    env : Environment
        Account and region
    sds_id : str
        Name suffix for stack
    """
    open_search = opensearch_stack.OpenSearch(scope, f"OpenSearch-{sds_id}",
                                              sds_id, env=env)

    data_manager = sds_data_manager_stack.SdsDataManager(scope, f"SdsDataManager-{sds_id}",
                                                         sds_id,
                                                         open_search,
                                                         env=env)

    # lambdas = lambda_stack.OpenSearchLambdas(scope, f"LambdaStack-{sds_id}",
    #                                          sds_id,
    #                                          open_search,
    #                                          data_manager,
    #                                          env=env)

    domain = domain_stack.Domain(scope, f"DomainStack-{sds_id}",
                                 sds_id, env=env)

    api_gateway_stack.ApiGateway(scope, f"ApiGateway-{sds_id}",
                                 sds_id, data_manager.lambda_functions,
                                 domain.hosted_zone, domain.certificate, env=env)
