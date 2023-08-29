from pathlib import Path

from aws_cdk import (
    Duration,
    Environment,
    Stack,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_stepfunctions as sfn,
)
from aws_cdk import (
    aws_stepfunctions_tasks as tasks,
)
from constructs import Construct

from sds_data_manager.stacks import (
    lambda_stack,
)


class ProcessingStepFunctionStack(Stack):
    def __init__(
        self,
        scope: Construct,
        id: str,
        sds_id: str,
        env: Environment,
        dynamodb_table_name: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, id, env=env, **kwargs)
        """
        This stack creates lambda functions that will be invoked in the processing
        step function. Then it creates step functions task for those lambdas and
        creates a state machine definition to run those tasks in sequence.

        Parameters
        ----------
        scope : Construct
            The scope of the CDK construct.
        id : str
            The ID of the CDK construct.
        env : Environment
            The environment of the CDK construct. It contains account number and region.
        dynamodb_table_name : str
            The name of the DynamoDB table.
        kwargs : dict
            Other parameters.
        """

        # Get AWS managed policies to attach to lambda role.
        aws_managed_lambda_permissions = [
            "service-role/AWSLambdaBasicExecutionRole",
            "AmazonS3FullAccess",
            "AmazonDynamoDBFullAccess",
        ]

        # Set path of main folder for lambda code.
        lambda_code_main_folder = f"{Path(__file__).parent}/../lambda_images/"

        # Set processing lambda code path. This path should contain Dockerfile.
        imap_processing_lambda_code_path = (
            f"{lambda_code_main_folder}/imap_processing_lambda/"
        )
        # Create a lambda function for processing.
        imap_processing_lambda = lambda_stack.LambdaWithDockerImageStack(
            scope,
            sds_id=f"ProcessingLambda-{sds_id}",
            lambda_name=f"processing-lambda-{sds_id}",
            managed_policy_names=aws_managed_lambda_permissions,
            timeout=300,
            lambda_code_folder=imap_processing_lambda_code_path,
        )

        # Set data checker lambda code path. This path should contain Dockerfile.
        data_checker_lambda_code_path = (
            f"{lambda_code_main_folder}/data_checker_lambda/"
        )
        # Create a lambda function for data checker.
        data_checker_lambda = lambda_stack.LambdaWithDockerImageStack(
            scope,
            sds_id=f"DataCheckerLambda-{sds_id}",
            lambda_name=f"data-checker-lambda-{sds_id}",
            managed_policy_names=aws_managed_lambda_permissions,
            timeout=300,
            lambda_code_folder=data_checker_lambda_code_path,
            lambda_environment_vars={"DYNAMODB_TABLE": dynamodb_table_name},
        )

        # Create the IAM role for the Step Functions state machine
        step_function_role = iam.Role(
            self,
            "MyStateMachineRole",
            assumed_by=iam.ServicePrincipal("states.amazonaws.com"),
            description="IAM role for the Step Functions state machine",
        )
        # Add a policy statement to the role
        lambda_invoke_policy_statement = iam.PolicyStatement(
            effect=iam.Effect.ALLOW, actions=["lambda:InvokeFunction"], resources=["*"]
        )

        # Attach the policy statement to the role
        step_function_role.add_to_policy(lambda_invoke_policy_statement)

        # This lambda task invokes processing lambda

        # Note: sfn.TaskInput.from_json_path_at("$") is used to get the step
        # function input and pass it as input to the processing lambda.
        # Then result_path is used to pass down step function input to
        # next step function task.
        # result_selector is used to select the result from the processing
        # lambda output.
        processing_task = tasks.LambdaInvoke(
            self,
            "Decom Lambda",
            lambda_function=imap_processing_lambda.fn,
            payload=sfn.TaskInput.from_json_path_at("$"),
            result_path="$.Payload",
            result_selector={
                "status": sfn.JsonPath.string_at("$.Payload.status"),
            },
        )
        # This lambda task invokes data checker lambda
        checker_task = tasks.LambdaInvoke(
            self,
            "DataCheckerTask Lambda",
            lambda_function=data_checker_lambda.fn,
            payload=sfn.TaskInput.from_json_path_at("$"),
            result_path="$.Payload",
            result_selector={
                "status_code": sfn.JsonPath.string_at("$.Payload.status_code"),
            },
        )

        # fail and success states
        success_state = sfn.Succeed(self, "SuccessState")
        empty_state = sfn.Fail(
            self, "No Data to Process", cause="No data to process", error="EmptyError"
        )
        invalid_status_state = sfn.Fail(
            self,
            "Invalid Status Code",
            cause="Invalid status code",
            error="InvalidStatusCodeError",
        )
        sfn.Fail(
            self,
            "Processing Failed",
            cause="Failed to process",
            error="FailedProcessError",
        )
        not_supported = sfn.Fail(
            self,
            "Not Supported",
            cause="Instrument not supported",
            error="NotSupportedError",
        )

        # Define the state machine definition
        # IMAP processing lambda returns status. This Choice path
        # checks status and based on status invokes fail or succes state.
        process_status = sfn.Choice(self, "Processing status?")
        process_status.when(
            sfn.Condition.string_equals("$.Payload.status", "SUCCEEDED"), success_state
        ).when(sfn.Condition.string_equals("$.Payload.status", "FAILED"), not_supported)

        # Data checker lambda returns status code. This Choice path
        # checks status code and based on status code invokes fail or next state.
        # Otherwise it invokes invalid status state if status code is not 200 or 204
        data_checker = sfn.Choice(self, "Data Status Check?")
        data_checker.when(
            sfn.Condition.number_equals("$.Payload.status_code", 200),
            processing_task.next(process_status),
        ).when(
            sfn.Condition.number_equals("$.Payload.status_code", 204), empty_state
        ).otherwise(
            invalid_status_state
        )

        # Define state machine definition. This determines process flow
        # on step function.
        definition = checker_task.next(data_checker)

        # Create the Step Functions state machine
        self.sfn = sfn.StateMachine(
            self,
            "MyStateMachine",
            state_machine_name=f"processing-state-machine-{sds_id}",
            definition=definition,
            timeout=Duration.minutes(5),
            role=step_function_role,
        )
