"""Configure monitoring formatter lambda."""

import aws_cdk as cdk
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from constructs import Construct


class MonitoringLambda(Construct):
    """Construct for monitoring lambda."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        code: lambda_.Code,
        sns_topic,
        **kwargs,
    ) -> None:
        """MonitoringLambda Construct.

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        code : aws_lambda.Code
            Lambda code bundle
        sns_topic : aws_sns.Topic
            SNS Topic for sending notifications so that external
            resources can subscribe to for alerts.
        kwargs : dict
            Keyword arguments

        """
        super().__init__(scope, construct_id, **kwargs)

        monitoring_lambda = lambda_.Function(
            self,
            id="MonitoringLambda",
            function_name="monitoring",
            code=code,
            handler="SDSCode.pipeline_lambdas.monitoring.lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_12,
            timeout=cdk.Duration.minutes(1),
            memory_size=1000,
            environment={
                "SNS_TOPIC_ARN": sns_topic.topic_arn,
            },
            allow_public_subnet=True,
            architecture=lambda_.Architecture.ARM_64,
        )

        # Uses batch job status of failure
        # to trigger a sns topic
        batch_job_failure_rule = events.Rule(
            self,
            "batchJobFailure",
            rule_name="batch-job-failed",
            event_pattern=events.EventPattern(
                source=["aws.batch"],
                detail_type=["Batch Job State Change"],
                detail={"status": ["FAILED"]},
            ),
        )

        # monitoring lambda will retrieve logs and publish output to SNS
        sns_topic.grant_publish(monitoring_lambda)

        monitoring_lambda.add_to_role_policy(
            iam.PolicyStatement(
                actions=["logs:GetLogEvents"],
                resources=["arn:aws:logs:*:*:log-group:/aws/batch/*"],
            )
        )

        batch_job_failure_rule.add_target(targets.LambdaFunction(monitoring_lambda))
