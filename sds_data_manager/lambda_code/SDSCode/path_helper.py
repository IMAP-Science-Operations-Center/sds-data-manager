"""Functions to store file pattern, validate and construct upload path
"""
import re
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class FilenamePatternConfig:
    """This class stores filename pattern configuration."""

    mission: str = "imap"
    instruments: list = field(
        default_factory=lambda: [
            "codice",
            "glows",
            "hit",
            "hi-45",
            "idex",
            "lo",
            "mag",
            "swapi",
            "swe",
            "ultra-45",
        ]
    )
    data_level: list = field(
        default_factory=lambda: ["l0", "l1", "l1a", "l1b", "l1c", "l1d", "l2"]
    )
    descriptor: str = None
    startdate: str = "YYYYMMDD"
    enddate: str = "YYYYMMDD"
    version: str = r"^v\d{2}-\d{2}$"
    extensions: list = field(default_factory=lambda: ["pkts", "cdf"])


class FilenameParser:
    def __init__(self, filename):
        """Stores common methods to check
        file patterns and build upload path.

        Current filename convention:
        <mission>_<instrument>_<datalevel>_<descriptor>_<startdate>_<enddate>_<version>.<extension>

        NOTE: There are no optional parameters anymore. All parameters are required.
        <mission>: imap
        <instrument>: idex, swe, swapi, hi-45, ultra-45 and etc.
        <datalevel> : l1a, l1b, l1, l3a and etc.
        <descriptor>: descriptor stores information specific to instrument. This is
            decided by each instrument.
        <startdate>: startdate is the earliest date in the data. Format - YYYYMMDD
        <enddate>: Some instrument and some data level requires to store date range.
            If there is no end date, then startdate will be used as enddate as well.
            Format - YYYYMMDD.
        <version>: This stores software version and data version. Version format is
            vxx-xx.

        Parameters
        ----------
        filename : str
            Filename
        """
        self.filename = filename
        self.filename_convention = (
            "<mission>_<instrument>_<datalevel>_<descriptor>_"
            "<startdate>_<enddate>_<version>.<extension>"
        )
        self.split_filename = filename.split("_")
        (
            self.mission,
            self.instrument,
            self.data_level,
            self.descriptor,
            self.startdate,
            self.enddate,
            self.last_value,
        ) = self.split_filename
        (self.version, self.extension) = self.last_value.split(".")
        # This message is returned to user through API to indicate why filename was not
        # correct.
        self.message = None

    def check_date_input(self, input_date: str) -> bool:
        """Check input date string is in valid format and is correct date

        Parameters
        ----------
        input_date : str
            Date in YYYYMMDD format.

        Returns
        -------
        bool
            Whether date input is valid or not
        """

        # Validate if it's a real date
        try:
            # This checks if date is in YYYYMMDD format.
            # Sometimes, date is correct but not in the format we want
            if len(input_date) != 8:
                raise ValueError("Invalid date format.")
            datetime.strptime(input_date, "%Y%m%d")
            return True
        except ValueError:
            return False

    def validate_filename(self, file_pattern_config=None) -> bool:
        """Validate filename patterns.

        Parameters
        ----------
        file_pattern_config : FilenamePatternConfig
            Filename pattern configuration. Default is FilenamePatternConfig().
            This is used to validate filename. If you want to validate filename
            for your own purpose, you can pass your own configuration.
            See FilenamePatternConfig class for more details.

        Returns
        -------
        bool
            Whether filename format is valid or not
        """
        if file_pattern_config is None:
            file_pattern_config = FilenamePatternConfig()

        # First check if any of parameter is missing
        if any(
            attr is None or attr == ""
            for attr in [
                self.mission,
                self.instrument,
                self.data_level,
                self.descriptor,
                self.startdate,
                self.enddate,
                self.version,
                self.extension,
            ]
        ):
            return False

        # Dictionary to map fields to their valid values and error messages
        validation_checks = {
            "mission": (
                self.mission == file_pattern_config.mission,
                "Invalid mission.",
            ),
            "instrument": (
                self.instrument in file_pattern_config.instruments,
                (
                    "Invalid instrument. Please choose from "
                    f"{file_pattern_config.instruments}"
                ),
            ),
            "data_level": (
                self.data_level in file_pattern_config.data_level,
                (
                    "Invalid data level. Please choose from "
                    f"{file_pattern_config.data_level}"
                ),
            ),
            "startdate": (
                self.check_date_input(self.startdate),
                "Invalid start date format. Please use YYYYMMDD format.",
            ),
            "enddate": (
                self.check_date_input(self.enddate),
                "Invalid end date format. Please use YYYYMMDD format.",
            ),
            "version": (
                bool(re.match(file_pattern_config.version, self.version)),
                "Invalid version format. Please use vxx-xx format.",
            ),
            "extension": (
                self.extension in file_pattern_config.extensions
                and (
                    (self.data_level == "l0" and self.extension == "pkts")
                    or (self.data_level != "l0" and self.extension == "cdf")
                ),
                (
                    "Invalid extension. Extension should be pkts for data level l0 "
                    "and cdf for data level higher than l0"
                ),
            ),
        }

        # Iterate through each validation check
        for _field, (is_valid, error_message) in validation_checks.items():
            if not is_valid:
                self.message = error_message
                return False
        return True

    def create_path_to_upload(self) -> str:
        """Create upload path to S3 bucket.

        path to upload file follows this format:
        mission/instrument/data_level/year/month/filename
        NOTE: year and month is from startdate and startdate format is YYYYMMDD.

        Returns
        -------
        str
            path to upload file
        """
        path_to_upload_file = (
            f"{self.mission}/{self.instrument}/{self.data_level}/"
            f"{self.startdate[:4]}/{self.startdate[4:6]}/{self.filename}"
        )

        return path_to_upload_file

    def upload_filepath(self):
        """Return upload path or error message.

        Returns
        -------
        dict
            Upload path or error message
        """
        if not self.validate_filename():
            default_message = (
                f"Invalid filename. Filename convention is {self.filename_convention}"
            )
            return {"statusCode": 400, "body": self.message or default_message}
        return {"statusCode": 200, "body": self.create_path_to_upload()}
