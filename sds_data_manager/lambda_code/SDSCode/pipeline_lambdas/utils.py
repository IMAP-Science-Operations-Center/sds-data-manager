"""Common fuctions for pipeline lambdas."""

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional, Dict, Any


@dataclass
class DependencyNode:
    """Shared between batch starter and dependency lambda."""

    source: str
    data_type: str
    product_name: str

    def serialize(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def deserialize(cls, json_object: Dict[str, Any]):
        return cls(**json_object)

@dataclass
class UpstreamDependencyNode(DependencyNode):
    start_date: datetime
    end_date: datetime
    # These optional needs to go after required fields
    # to line with dataclass rules.
    reprocessing: bool = False
    repoint: Optional[int] = None
