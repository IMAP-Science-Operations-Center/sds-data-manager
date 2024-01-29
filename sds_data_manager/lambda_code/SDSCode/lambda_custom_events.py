"""Stores custom EventBridge PutEvent structure"""
import json
from dataclasses import dataclass


@dataclass
class IMAPLambdaPutEvent:
    detail_type: str
    detail: dict
    source: str = "imap.lambda"

    def to_event(self):
        return {
            "DetailType": self.detail_type,
            "Source": self.source,
            "Detail": json.dumps(self.detail),
        }
