"""
The state machine integrates with AWS Batch to execute processing
components.
"""

from aws_cdk import Stack
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import aws_stepfunctions_tasks as tasks
from constructs import Construct

from sds_data_manager.constructs.batch_compute_resources import FargateBatchResources


class SdcStepFunction(Construct):
    """Step Function Construct

    Creates state machine using processing components.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        processing_step_name: str,
        batch_resources: FargateBatchResources,
        data_bucket: s3.Bucket,
        db_secret_name: str,
    ):
        """SdcStepFunction Constructor

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        processing_step_name : str
            The string identifier for the processing step
        batch_resources: FargateBatchResources
            Fargate compute environment
        data_bucket : s3.Bucket
            S3 bucket
        db_secret_name : str
            Db secret name

        """
        super().__init__(scope, construct_id)

        self.execution_policy = iam.PolicyStatement(
            effect=iam.Effect.ALLOW, actions=["states:StartExecution"], resources=["*"]
        )

        # Reformat Lambda Inputs
        add_specifics_to_input = sfn.Pass(
            self,
            "Reformat Lambda Inputs",
            parameters={
                "COMMAND.$": "$.command",
            },
        )

        # Batch Job Inputs
        stack = Stack.of(self)
        job_definition_arn = (
            f"arn:aws:batch:{stack.region}:{stack.account}:job-definition/"
            f"{batch_resources.job_definition_name}"
        )
        job_queue_arn = (
            f"arn:aws:batch:{stack.region}:{stack.account}:job-queue/"
            f"{batch_resources.job_queue_name}"
        )

        # Batch Job
        submit_job = tasks.BatchSubmitJob(
            self,
            f"BatchJob-{processing_step_name}",
            job_name=processing_step_name,
            job_definition_arn=job_definition_arn,
            job_queue_arn=job_queue_arn,
            container_overrides=tasks.BatchContainerOverrides(
                command=sfn.JsonPath.list_at("$.COMMAND"),
                environment={
                    "OUTPUT_PATH": data_bucket.bucket_name,
                    "SECRET_NAME": db_secret_name,
                },
            ),
            result_path="$.BatchJobOutput",
        )

        # Success and Fail Final States
        fail_state = sfn.Fail(self, "Fail State")
        submit_job.add_catch(fail_state)

        # State sequences
        add_specifics_to_input.next(submit_job)

        # Define the state machine
        definition_body = sfn.DefinitionBody.from_chainable(add_specifics_to_input)
        self.state_machine = sfn.StateMachine(
            self,
            f"CDKProcessingStepStateMachine-{processing_step_name}",
            definition_body=definition_body,
            state_machine_name=f"{processing_step_name}-step-function",
        )
