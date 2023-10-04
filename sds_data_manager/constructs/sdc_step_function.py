"""
The state machine integrates with AWS Lambda and AWS Batch to execute processing
components.

Key Features:
- Configures AWS Step Functions tasks to invoke specific Lambda functions.
- Dynamically constructs ARNs for Batch job definitions and queues.
- Handles branching logic based on the success or failure of previous steps.
- Defines a comprehensive state machine for the entire data processing flow.
"""
from aws_cdk import Stack
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import aws_stepfunctions_tasks as tasks
from constructs import Construct

from sds_data_manager.constructs.batch_compute_resources import FargateBatchResources
from sds_data_manager.constructs.instrument_lambdas import InstrumentLambda


class SdcStepFunction(Construct):
    """Step Function Construct

    Creates state machine using processing components.
    """

    def __init__(self,
                 scope: Construct,
                 construct_id: str,
                 processing_step_name: str,
                 processing_system: InstrumentLambda,
                 batch_resources: FargateBatchResources,
                 instrument_target: str,
                 data_bucket: s3.Bucket):
        """SdcStepFunction Constructor

        Parameters
        ----------
        scope : Construct
        construct_id : str
        processing_step_name : str
            The string identifier for the processing step
        processing_system: BatchProcessingSystem
            Batch processing system
        batch_resources: FargateBatchResources
            Fargate compute environment
        instrument_target : str
            Target data product
        data_bucket : str
            S3 bucket
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
        instrument_task = tasks.LambdaInvoke(self,
                                             f"InstrumentLambda-{processing_step_name}",
                                             lambda_function=processing_system.instrument_lambda,
                                             payload=sfn.TaskInput.from_object(
                                                 {"TIMEOUT_TIME.$": "$.TIMEOUT_TIME"}),
                                             result_path="$.InstrumentOutput",
                                             result_selector={
                                                 "STATE.$": "$.Payload.STATE",
                                                 "JOB_NAME.$": "$.Payload.JOB_NAME",
                                                 "COMMAND.$": "$.Payload.COMMAND",
                                                 "OUTPUT_PATH": "$.Payload.OUTPUT_PATH",
                                                 "INSTRUMENT_TARGET":
                                                     "$.Payload.INSTRUMENT_TARGET"
                                             })

        # Batch Job Inputs
        stack = Stack.of(self)
        job_definition_arn = \
            f'arn:aws:batch:{stack.region}:{stack.account}:job-definition/' \
            f'{batch_resources.job_definition_name}'
        job_queue_arn = f'arn:aws:batch:{stack.region}:{stack.account}:job-queue/' \
                        f'{batch_resources.job_queue_name}'

        instrument_target = f"{instrument_target}"

        # Batch Job
        submit_job = tasks.BatchSubmitJob(
            self, f"BatchJob-{processing_step_name}",
            job_name=sfn.JsonPath.string_at("$.InstrumentOutput.JOB_NAME"),
            job_definition_arn=job_definition_arn,
            job_queue_arn=job_queue_arn,
            container_overrides=tasks.BatchContainerOverrides(
                command=sfn.JsonPath.list_at("$.InstrumentOutput.COMMAND"),
                environment={
                    "OUTPUT_PATH": data_bucket.bucket_name,
                    "INSTRUMENT_TARGET": instrument_target
                }
            ),
            result_path='$.BatchJobOutput'
        )

        # Success and Fail Final States
        fail_state = sfn.Fail(self, "Fail State")

        # Choice State
        instrument_status = sfn.Choice(self, "Success?")
        # Go to Batch job
        created = sfn.Condition.string_equals("$.InstrumentOutput.STATE", "SUCCESS")
        instrument_status.when(created, submit_job)
        instrument_status.otherwise(fail_state)

        submit_job.add_catch(fail_state)

        # State sequences
        add_specifics_to_input.next(
            instrument_task).next(
            instrument_status)

        # Define the state machine
        definition_body = sfn.DefinitionBody.from_chainable(add_specifics_to_input)
        self.state_machine = sfn.StateMachine(self,
                                              f"CDKProcessingStepStateMachine-{processing_step_name}",
                                              definition_body=definition_body,
                                              state_machine_name=f"{processing_step_name}-step-function")
