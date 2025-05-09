"""Contains the lambda handler for the 'query' data access API."""

import json
import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Optional

from . import spice_query_api
from .metakernel import MetaKernel

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class LeapsecondKernels(Enum):
    """Container for Leapsecond Kernel Types."""

    LEAPSECONDS = auto()

    @staticmethod
    def spice_category_name():
        """Category of SPICE file."""
        return "leapseconds_category"


class PlanetaryConstantsKernels(Enum):
    """Container for Planetary Contants Kernel Types."""

    PLANETARY_CONSTANTS = auto()

    @staticmethod
    def spice_category_name():
        """Category of SPICE file."""
        return "planetary_constants_category"


class FramesKernels(Enum):
    """Container for Frames Kernel Types."""

    FRAMES = auto()

    @staticmethod
    def spice_category_name():
        """Category of SPICE file."""
        return "frames_category"


class SpacecraftClockKernels(Enum):
    """Container for Spacecraft Clock Kernel Types."""

    SPACECRAFT_CLOCK = auto()

    @staticmethod
    def spice_category_name():
        """Category of SPICE file."""
        return "spacecraft_clock_category"


class PlanetaryEphemerisKernels(Enum):
    """Container for Planetary Ephemeris Kernel Types."""

    PLANETARY_EPHEMERIS = auto()

    @staticmethod
    def spice_category_name():
        """Category of SPICE file."""
        return "planetary_ephemeris_category"


class SpacecraftEphemerisKernels(Enum):
    """Container for Spacecraft Ephemeris Kernel Types."""

    EPHEMERIS_RECONSTRUCTED = auto()
    EPHEMERIS_NOMINAL = auto()
    EPHEMERIS_PREDICTED = auto()
    EPHEMERIS_90DAYS = auto()
    EPHEMERIS_LONG = auto()
    EPHEMERIS_LAUNCH = auto()

    @staticmethod
    def spice_category_name():
        """Category of SPICE file."""
        return "spacecraft_ephemeris_category"


class SpacecraftAttitudeKernels(Enum):
    """Container for Spacecraft Attitude Kernel Types."""

    ATTITUDE_HISTORY = auto()
    ATTITUDE_PREDICT = auto()

    @staticmethod
    def spice_category_name():
        """Category of SPICE file."""
        return "spacecraft_attitude_category"


class PointingAttitudeKernels(Enum):
    """Container for Pointing Attitude Kernel Types."""

    POINTING_ATTITUDE = auto()

    @staticmethod
    def spice_category_name():
        """Category of SPICE file."""
        return "pointing_attitude_category"


@dataclass
class KernelCollection:
    """Collection of SPICE kernel types for IMAP."""

    imap_spice_load_order: list = field(
        default_factory=lambda: [
            LeapsecondKernels,
            PlanetaryConstantsKernels,
            FramesKernels,
            SpacecraftClockKernels,
            PlanetaryEphemerisKernels,
            SpacecraftEphemerisKernels,
            SpacecraftAttitudeKernels,
            PointingAttitudeKernels,
        ]
    )

    @property
    def file_types(self):
        """Return all kernel members in lowercase."""
        members = []
        for kernel_class in self.imap_spice_load_order:
            members.extend([member.name.lower() for member in kernel_class])
        return members

    @property
    def category_types(self):
        """Collect all kernel category type strings."""
        return [
            kernel_class.spice_category_name()
            for kernel_class in self.imap_spice_load_order
        ]


def lambda_handler(event, context):
    """Entry point to the SPICE query API lambda.

    Parameters
    ----------
    event : dict
        The JSON formatted document with the data required for the
        lambda function to process
    context : LambdaContext
        This object provides methods and properties that provide
        information about the invocation, function,
        and runtime environment.

    """
    logger.info(f"Event: {event}")
    logger.info(f"Context: {context}")

    logger.info("Received event: " + json.dumps(event, indent=2))

    # Gather the query parameters
    query_params = event["queryStringParameters"]
    start_time = query_params["start_time"]
    end_time = query_params["end_time"]
    spice_directory = Path(query_params.get("spice_path", ""))
    list_files = query_params.get("list_files", "false")
    require_coverage = query_params.get("require_coverage", "false")
    file_types = query_params.get("file_types", None)
    if file_types:
        file_types = {type.strip().upper() for type in file_types.split(",")}

    # Build a metakernel
    metakernel = _metakernel_builder(start_time, end_time, file_types=file_types)

    if (require_coverage.lower() == "true") and metakernel.contains_gaps():
        return {
            "statusCode": 422,  # Unprocessable Content
            "body": json.dumps(metakernel.spice_gaps),
            "headers": {
                "Content-Type": "application/json",
                "Access-Control-Allow-Origin": "*",  # Allow CORS
            },
        }

    if list_files.lower() == "true":
        metakernel_files = metakernel.return_spice_files_in_order(detailed=False)
        output = json.dumps([Path(f).name for f in metakernel_files])
    else:
        output = metakernel.return_tm_file(base_path=spice_directory)

    # Format the response
    response = {
        "statusCode": 200,
        "body": output,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",  # Allow CORS
        },
    }

    return response


def _metakernel_builder(
    start_time: int, end_time: int, file_types: Optional[list] = None
) -> MetaKernel:
    """Create a MetaKernel class and inserts files into it."""
    # Create the Metakernel class
    metakernel = MetaKernel(
        start_time,
        end_time,
        allowed_spice_types=KernelCollection().category_types,
    )

    for spice_category in KernelCollection().imap_spice_load_order:
        for spice_subtype in spice_category:
            if file_types and spice_subtype.name not in file_types:
                continue  # Skip over the file if not in requested list
            spice_files = spice_query_api.lambda_handler(
                {
                    "queryStringParameters": {
                        "start_time": start_time,
                        "end_time": end_time,
                        "type": spice_subtype.name.lower(),
                        "latest": "True",
                    }
                },
                None,
            )
            metakernel.load_spice(
                json.loads(spice_files["body"]),
                spice_category.spice_category_name(),
                "file_intervals_j2000",
                priority_field="timestamp",
            )

    return metakernel
