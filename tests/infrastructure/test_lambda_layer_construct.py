"""Testing for the lambda layer builder."""

from pathlib import Path

from aws_cdk.assertions import Template

from sds_data_manager.constructs.lambda_layer_construct import IMAPLambdaLayer


def test_lambda_layer_creation(stack):
    """Lambda layer tests."""
    lambda_code_directory = (
        Path(__file__).parent.parent.parent / "lambda_layer/python"
    ).resolve()
    IMAPLambdaLayer(
        scope=stack,
        id="TestDependencies",
        layer_dependencies_dir=str(lambda_code_directory),
    )

    template = Template.from_stack(stack)
    template.resource_count_is("AWS::Lambda::LayerVersion", 1)
