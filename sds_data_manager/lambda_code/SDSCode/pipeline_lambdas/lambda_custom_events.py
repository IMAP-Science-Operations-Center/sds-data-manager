"""Store custom EventBridge PutEvent structure."""

import json
from dataclasses import dataclass


@dataclass
class IMAPLambdaPutEvent:
    """Data class for lambda PutEvent."""

    detail_type: str
    detail: dict
    source: str = "imap.lambda"

    def to_event(self):
        """Return the event details."""
        return {
            "DetailType": self.detail_type,
            "Source": self.source,
            "Detail": json.dumps(self.detail),
        }
