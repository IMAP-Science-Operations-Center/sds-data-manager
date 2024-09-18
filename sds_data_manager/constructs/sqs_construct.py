"""Construct for creating instrument queues and attaching batch_starter as a target."""

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
            visibility_timeout=Duration.seconds(300),
            # This is required. It removes messages with identical content. Since
            # the event includes a filename each event should be totally unique.
            content_based_deduplication=True,
            # The dead letter queue will take messages that failed retry 20 times.
            dead_letter_queue=aws_sqs.DeadLetterQueue(
                max_receive_count=20, queue=self.dead_letter_queue
            ),
        )

        for instrument_name in instrument_names:
            # Event has filename in it, we need an EventPattern that matches that
            # EventBridge Rule for the SQS queue
            event_from_indexer = events.Rule(
                self,
                f"{instrument_name}FileArrived",
                rule_name=f"{instrument_name}_file_arrived",
                event_pattern=events.EventPattern(
                    source=["imap.lambda"],
                    detail_type=["Processed File"],
                    detail={
                        "object": {
                            "key": [{"exists": True}],
                            "instrument": [instrument_name],
                        },
                    },
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
