"""Configure the monitoring.

Currently sets up an integration with Slack to send notifications to a channel.
This is done through an SNS Topic and AWS Chatbot.

This had to be done manually in the AWS console to authorize the
AWS account to use the Slack app.
Just follow the steps here to create a new Slack authorization.
https://us-east-2.console.aws.amazon.com/chatbot/home
Choosing the channel and permissions as you go through the steps.

More monitoring integrations can be added here in the future.
"""

from aws_cdk import aws_sns
from constructs import Construct


class MonitoringConstruct(Construct):
    """Define the monitoring stack components."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        **kwargs,
    ) -> None:
        """Construct for monitoring resources within AWS.

        Parameters
        ----------
        scope : Construct
            Parent construct.
        construct_id : str
            A unique string identifier for this construct.
        kwargs : dict
            Keyword arguments

        Attributes
        ----------
        sns_topic_notifications : aws_sns.Topic
            SNS Topic for sending notifications so that external
            resources can subscribe to for alerts.

        """
        super().__init__(scope, construct_id, **kwargs)
        self.sns_topic_notifications = aws_sns.Topic(
            self, "sns-notifications", display_name="sns-notifications"
        )
