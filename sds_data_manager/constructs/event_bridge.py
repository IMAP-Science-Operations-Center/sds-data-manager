from constructs import Construct
from aws_cdk import (
    aws_iam as iam,
    aws_stepfunctions as sfn,
    aws_events as events,
    aws_events_targets as targets,
    aws_s3 as s3,
)


class S3EventbridgeStepFunctionsProps:
    def __init__(self,
                 state_machine: sfn.IStateMachine,
                 state_machine_input: dict,
                 archive_bucket: s3.IBucket,
                 instrument_sources: str):
        self.state_machine = state_machine
        self.state_machine_input = state_machine_input
        self.archive_bucket = archive_bucket
        self.instrument_sources = instrument_sources


class S3EventbridgeStepFunctions(Construct):
    def __init__(self, scope: Construct, id: str, props: S3EventbridgeStepFunctionsProps):
        super().__init__(scope, id)

        # Create an EventBridge rule to trigger on S3 object creations
        self.event_rule = events.Rule(self, "EventsRule",
            event_pattern=events.EventPattern(
                source=["aws.s3"],
                detail_type=["Object Created"],
                detail={
                    "bucket": {
                        "name": [props.archive_bucket.bucket_name]
                    }
                }
            )
        )

        # IAM role for EventBridge to start the state machine
        event_role = iam.Role(
            self,
            "eventRole",
            assumed_by=iam.ServicePrincipal("events.amazonaws.com")
        )
        props.state_machine.grant_start_execution(event_role)

        # Add the state machine as the target for the EventBridge rule
        self.event_rule.add_target(targets.SfnStateMachine(
            props.state_machine,
            input=events.RuleTargetInput.from_object(props.state_machine_input),
            role=event_role
        ))
