"""Common fuctions for pipeline lambdas."""

from datetime import datetime
from typing import Optional


class DependencyNode:
    """This is shared between batch starter and dependency lambda."""

    source: str
    data_type: str
    product_name: str
    start_date: datetime
    end_date: datetime
    reprocessing: bool = False
    repoint: Optional[int] = None

    def __init__(
        self,
        source: str,
        data_type: str,
        product_name: str,
        start_date: datetime,
        end_date: datetime,
        reprocessing: bool = False,
        repoint: Optional[int] = None,
    ):
        self.source = source
        self.data_type = data_type
        self.product_name = product_name
        self.start_date = start_date
        self.end_date = end_date
        self.reprocessing = reprocessing
        self.repoint = repoint

    def serialize(self):
        return {
            "source": self.source,
            "data_type": self.data_type,
            "product_name": self.product_name,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "reprocessing": self.reprocessing,
            "repoint": self.repoint,
        }

    @classmethod
    def deserialize(cls, json_object):
        return cls(
            source=json_object["source"],
            data_type=json_object["data_type"],
            product_name=json_object["product_name"],
            start_date=json_object["start_date"],
            end_date=json_object["end_date"],
            reprocessing=json_object["reprocessing"],
            repoint=json_object.get("repoint", None),
        )
