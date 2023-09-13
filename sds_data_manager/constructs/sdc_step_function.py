from constructs import Construct
from aws_cdk import (
    Duration,
    Stack,
    aws_events as events,
    aws_events_targets as event_targets,
    aws_lambda,
    aws_stepfunctions as sfn,
    aws_stepfunctions_tasks as tasks,
    aws_sns as sns,
    aws_sns_subscriptions as subscriptions
)
from sds_data_manager.constructs.batch_compute_resources import FargateBatchResources
from sds_data_manager.constructs.batch_system import BatchProcessingSystem


class SdcStepFunction(Construct):
    """Step Function Construct

    Creates state machine using processing components.
    """

    def __init__(self,
                 scope: Construct,
                 construct_id: str,
                 sds_id: str,
                 processing_step_name: str,
                 processing_system: BatchProcessingSystem,
                 batch_resources: FargateBatchResources):
        """SdcStepFunction Constructor

        Parameters
        ----------
        scope : Construct
        construct_id : str
        sds_id : str
            Name suffix for stack
        processing_step_name : str
            The string identifier for the processing step
        processing_system: BatchProcessingSystem
            Batch processing system.
        db_secret_name : str
            The string secret id that can be used to retrieve from the SecretManager
        batch_resources: FargateBatchResources
            Fargate compute environment.
        """
        super().__init__(scope, construct_id)

        # Reformat EventBridge Inputs
        add_specifics_to_input = sfn.Pass(
            self, "Reformat EventBridge Inputs",
            parameters={
                "TIMEOUT_TIME.$": "$.time",
            }
        )

        # Step Functions Tasks to invoke Lambda function
        manifest_creator_task = tasks.LambdaInvoke(self, "Manifest Creator",
                                                   lambda_function=processing_system.manifest_creator_lambda.lambda_function,
                                                   payload=sfn.TaskInput.from_object(
                                                       {"TIMEOUT_TIME.$": "$.TimeoutCreatorOutput.TIMEOUT_TIME"}),
                                                   result_path="$.ManifestCreatorOutput",
                                                   result_selector={
                                                       "MANIFEST_STATE.$": "$.Payload.MANIFEST_STATE",
                                                       "INPUT_MANIFEST_FILENAME.$": "$.Payload.INPUT_MANIFEST_FILENAME",
                                                       "JOB_NAME.$": "$.Payload.JOB_NAME",
                                                       "COMMAND.$": "$.Payload.COMMAND",
                                                       "PERCENT_COMPLETE.$": "$.Payload.PERCENT_COMPLETE",
                                                       "DATA_PRODUCT_NAME.$": "$.Payload.DATA_PRODUCT_NAME",
                                                       "OUTPUT_MANIFEST_FILENAME.$": "$.Payload.OUTPUT_MANIFEST_FILENAME"
                                                   })

        # Batch Job Inputs
        stack = Stack.of(self)
        job_definition_arn = \
            f'arn:aws:batch:{stack.region}:{stack.account}:job-definition/{batch_resources.job_definition_name}'
        job_queue_arn = f'arn:aws:batch:{stack.region}:{stack.account}:job-queue/{batch_resources.job_queue_name}'

        # Batch Job Step Function
        processing_dropbox_path_str = f"s3:/archive_bucket/processing"
        submit_job = tasks.BatchSubmitJob(
            self, "Batch Job",
            job_name=sfn.JsonPath.string_at("$.ManifestCreatorOutput.JOB_NAME"),
            job_definition_arn=job_definition_arn,
            job_queue_arn=job_queue_arn,
            container_overrides=tasks.BatchContainerOverrides(
                command=sfn.JsonPath.list_at("$.ManifestCreatorOutput.COMMAND"),
                environment={
                    "PROCESSING_DROPBOX": processing_dropbox_path_str
                }
            ),
            result_path='$.BatchJobOutput'
        )

        # Success and Fail Final States
        fail_state = sfn.Fail(self, "Fail State")

        # Manifest Choice State
        manifest_status = sfn.Choice(self, "Enough Files for Manifest Creation?")
        # Manifest created go to Batch job
        created = sfn.Condition.string_equals("$.ManifestCreatorOutput.MANIFEST_STATE", "FULL_SUCCESS")
        manifest_status.when(created, submit_job)
        manifest_status.otherwise(fail_state)

        submit_job.add_catch(fail_state)

        # State sequences
        add_specifics_to_input.next(
            manifest_creator_task).next(
            manifest_status)

        # Define the state machine
        definition_body = sfn.DefinitionBody.from_chainable(add_specifics_to_input)
        self.state_machine = sfn.StateMachine(self, "CDKProcessingStepStateMachine",
                                              definition_body=definition_body,
                                              state_machine_name=f"{processing_step_name}-processing-step-function")


