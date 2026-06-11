"""Construct for creating queues."""

from aws_cdk import Duration, aws_sqs
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from constructs import Construct


class SqsConstruct(Construct):
    """Construct to create instrument/level queues and attach them to EventBridge."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        instrument_names: list[str],
        **kwargs,
    ):
        """Create a SQS queue and Eventbridge rule for an instrument.

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        instrument_names : list[str]
            A list of all instrument names
        kwargs : dict
            Keyword arguments
        """
        super().__init__(scope, construct_id, **kwargs)

        # Create a dead letter queue to save messages that could not be processed.
        # This DLQ just saves the messages and doesn't do anything with them.
        self.dead_letter_queue = aws_sqs.Queue(
            self,
            "FileDeadLetterQueue",
            queue_name="file_dead_letter_queue.fifo",
            fifo=True,
            encryption=aws_sqs.QueueEncryption.UNENCRYPTED,
        )

        # This needs to be a FIFO queue to enforce ordering
        self.instrument_queue = aws_sqs.Queue(
            self,
            "FileArrivalQueue",
            queue_name="file_arrival_queue.fifo",
            fifo=True,
            encryption=aws_sqs.QueueEncryption.UNENCRYPTED,
            # This timeout determines how long the queue waits for processing. It must
            # be longer than the timeout of the lambda.
            visibility_timeout=Duration.seconds(900),
            # This is required. It removes messages with identical content. Since
            # the event includes a filename each event should be totally unique.
            content_based_deduplication=True,
            # The dead letter queue will take messages that failed retry.
            dead_letter_queue=aws_sqs.DeadLetterQueue(
                max_receive_count=1, queue=self.dead_letter_queue
            ),
        )

        # This queue is a special queue that delivers messages after a 15 minute delay
        # to avoid race conditions between files arriving. Currently, only MAG L1B are
        # in this queue.
        self.delay_queue = aws_sqs.Queue(
            self,
            "FileArrivalDelayQueue",
            queue_name="file_arrival_delay_queue.fifo",
            fifo=True,
            encryption=aws_sqs.QueueEncryption.UNENCRYPTED,
            # This timeout determines how long the queue waits for processing. It must
            # be longer than the timeout of the lambda.
            visibility_timeout=Duration.seconds(900),
            # This is required. It removes messages with identical content. Since
            # the event includes a filename each event should be totally unique.
            content_based_deduplication=True,
            # The dead letter queue will take messages that failed retry.
            dead_letter_queue=aws_sqs.DeadLetterQueue(
                max_receive_count=1, queue=self.dead_letter_queue
            ),
            delivery_delay=Duration.seconds(900),
        )

        delayed_cases = {"mag": "l1b"}

        for instrument_name in instrument_names:
            instrument_event_match = {
                "object": {
                    "key": [{"exists": True}],
                    "instrument": [instrument_name],
                },
            }

            if instrument_name in delayed_cases:
                data_level = delayed_cases[instrument_name]
                # Update main queue filters to not send delay queue events.
                instrument_event_match["object"]["data_level"] = [
                    {"anything-but": data_level}
                ]

                # Create another rule for delayed events based on instrument+level.
                delay_event = events.Rule(
                    self,
                    f"{instrument_name}FileArrived{data_level.upper()}",
                    rule_name=f"{instrument_name}_file_arrived_{data_level}",
                    event_pattern=events.EventPattern(
                        source=["imap.lambda"],
                        detail_type=["Processed File"],
                        detail={
                            "object": {
                                "key": [{"exists": True}],
                                "instrument": [instrument_name],
                                "data_level": [data_level],
                            },
                        },
                    ),
                )

                group_id = f"{instrument_name}_{data_level}"

                delay_event.add_target(
                    targets.SqsQueue(self.delay_queue, message_group_id=group_id)
                )

            # Ultra is split into ultra45 and ultra90 message groups based on sensor
            # type in the filename. L1 files use "45sensor"/"90sensor" and l2/l3 use
            # "u45"/"u90". L0 files have no sensor designation
            # and use the standard rule.
            if instrument_name == "ultra":
                ultra_45_rule = events.Rule(
                    self,
                    "ultraFileArrived45sensor",
                    rule_name="ultra_file_arrived_45sensor",
                    event_pattern=events.EventPattern(
                        source=["imap.lambda"],
                        detail_type=["Processed File"],
                        detail={
                            "object": {
                                "key": [
                                    {"wildcard": "*45sensor*"},
                                    {"wildcard": "*u45*"},
                                ],
                                "instrument": ["ultra"],
                            },
                        },
                    ),
                )
                ultra_45_rule.add_target(
                    targets.SqsQueue(self.instrument_queue, message_group_id="ultra45")
                )

                ultra_90_rule = events.Rule(
                    self,
                    "ultraFileArrived90sensor",
                    rule_name="ultra_file_arrived_90sensor",
                    event_pattern=events.EventPattern(
                        source=["imap.lambda"],
                        detail_type=["Processed File"],
                        detail={
                            "object": {
                                "key": [
                                    {"wildcard": "*90sensor*"},
                                    {"wildcard": "*u90*"},
                                ],
                                "instrument": ["ultra"],
                            },
                        },
                    ),
                )
                ultra_90_rule.add_target(
                    targets.SqsQueue(self.instrument_queue, message_group_id="ultra90")
                )

                # Restrict the standard rule below to l0 only, since 45/90 sensor
                # files are already routed above.
                instrument_event_match["object"]["data_level"] = ["l0"]

            # Event has filename in it, we need an EventPattern that matches that
            # EventBridge Rule for the SQS queue
            event_from_indexer = events.Rule(
                self,
                f"{instrument_name}FileArrived",
                rule_name=f"{instrument_name}_file_arrived",
                event_pattern=events.EventPattern(
                    source=["imap.lambda"],
                    detail_type=["Processed File"],
                    detail=instrument_event_match,
                ),
            )

            # Each rule points towards a new message_group_id within the file arrival
            # queue. The ordering is enforced only within the message_group_id, so
            # to scale up, just add additional rules and additional message_group_ids
            # here and everything will automatically scale.
            event_from_indexer.add_target(
                targets.SqsQueue(
                    self.instrument_queue, message_group_id=instrument_name
                )
            )
