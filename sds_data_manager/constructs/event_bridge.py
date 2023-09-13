from constructs import Construct

from aws_cdk import (
    aws_iam as iam,
    aws_stepfunctions as sfn,
    aws_events as events,
    aws_events_targets as targets,
    aws_s3 as s3,
    aws_sqs as sqs
)


class S3EventbridgeStepFunctionsProps:
    def __init__(self,
                 state_machine: sfn.IStateMachine,
                 state_machine_input: dict,
                 source_bucket: s3.IBucket,
                 event_pattern: events.EventPattern = None,
                 dead_letter_queue: bool = False
                 ):
        self.state_machine = state_machine
        self.state_machine_input = state_machine_input
        self.source_bucket = source_bucket
        self.event_pattern = event_pattern
        self.dead_letter_queue = dead_letter_queue


class S3EventbridgeStepFunctions(Construct):
    def __init__(self, scope: Construct, id: str, props: S3EventbridgeStepFunctionsProps):
        super().__init__(scope, id)

        props.source_bucket.enable_event_bridge_notification()

        self.event_rule = events.Rule(self, "EventsRule")

        if props.event_pattern:
            self.event_rule.add_event_pattern(props.event_pattern)
        else:
            self.event_rule.add_event_pattern(
                source=["aws.s3"],
                detail_type=["AWS API Call via CloudTrail"],
                detail={
                    "eventSource": ["s3.amazonaws.com"],
                    "eventName": ["PutObject"],
                    "requestParameters": {
                        "bucketName": [props.source_bucket.bucket_name]
                    }
                }
            )

        event_role = iam.Role(
            self,
            "eventRole",
            assumed_by=iam.ServicePrincipal("events.amazonaws.com")
        )

        props.state_machine.grant_start_execution(event_role)

        if not props.dead_letter_queue:
            self.event_rule.add_target(targets.SfnStateMachine(
                props.state_machine,
                input=events.RuleTargetInput.from_object(props.state_machine_input),
                role=event_role
            ))
        else:
            dlq = sqs.Queue(
                self,
                'DeadLetterQueue',
                encryption=sqs.QueueEncryption.SQS_MANAGED,
                enforce_ssl=True
            )
            self.event_rule.add_target(targets.SfnStateMachine(
                props.state_machine,
                input=events.RuleTargetInput.from_object(props.state_machine_input),
                dead_letter_queue=dlq,
                role=event_role
            ))
