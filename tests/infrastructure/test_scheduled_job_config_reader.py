"""Tests scheduled job config reader."""

import re
import textwrap
from unittest.mock import patch

import pytest

from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas import (
    scheduled_job_config_reader,
)


def test_scheduled_job_config_reader(tmp_path):
    """Tests the config reader."""
    config_path = tmp_path / "test_scheduled_jobs_config.csv"
    config = textwrap.dedent("""\
    # this is a comment
    cron(20 6 * * ? *), glows, l3b, descriptor1

    cron(20 14 * * ? *), glows, l3c, descriptor2
    cron(20 14 * * ? *), glows, l3d, descriptor3
    """)

    config_path.write_text(config, encoding="utf-8")
    with patch.object(scheduled_job_config_reader, "CONFIG_PATH", new=config_path):
        scheduled_jobs = scheduled_job_config_reader.read_scheduled_job_config()
        assert scheduled_jobs == {
            "cron(20 6 * * ? *)": [
                {
                    "data_source": "glows",
                    "data_type": "l3b",
                    "descriptor": "descriptor1",
                },
            ],
            "cron(20 14 * * ? *)": [
                {
                    "data_source": "glows",
                    "data_type": "l3c",
                    "descriptor": "descriptor2",
                },
                {
                    "data_source": "glows",
                    "data_type": "l3d",
                    "descriptor": "descriptor3",
                },
            ],
        }


def test_read_scheduled_job_config_throws_exception_for_unknown_instrument(tmp_path):
    """Tests config reader.

    Asserts that the config reader throws an
    exception with an unrecognized instrument.
    """
    config_path = tmp_path / "test_scheduled_jobs_config.csv"
    config = textwrap.dedent("""\
            cron(20 6 * * ? *), swapeeee, l3b, descriptor1
            """)

    config_path.write_text(config, encoding="utf-8")
    with patch.object(scheduled_job_config_reader, "CONFIG_PATH", new=config_path):
        with pytest.raises(ValueError, match="Invalid instrument: swapeeee"):
            scheduled_job_config_reader.read_scheduled_job_config()


def test_read_scheduled_job_config_throws_exception_for_incorrect_fields(tmp_path):
    """Tests config reader.

    Asserts that the config reader throws an
    exception with a wrong number of fields.
    """
    config_path = tmp_path / "test_scheduled_jobs_config.csv"

    cases = ["swapi, l3b, sci\n", "cron(20 6 * * ? *), swapi, l3b, sci, extra\n"]

    for case in cases:
        config_path.write_text(case, encoding="utf-8")
        with patch.object(scheduled_job_config_reader, "CONFIG_PATH", new=config_path):
            message = (
                "Each scheduled job should have ['schedule', 'instrument', "
                f"'data_level', 'descriptor']\nCurrent line: {case}"
            )
            with pytest.raises(ValueError, match=re.escape(message)):
                scheduled_job_config_reader.read_scheduled_job_config()


def test_read_scheduled_job_config_throws_exception_for_unknown_data_level(tmp_path):
    """Tests config reader.

    Asserts that the config reader throws an
    exception with an unrecognized data_level.
    """
    config_path = tmp_path / "test_scheduled_jobs_config.csv"
    config = "cron(20 6 * * ? *), swapi, l3bc, sci\n"

    config_path.write_text(config, encoding="utf-8")

    with patch.object(scheduled_job_config_reader, "CONFIG_PATH", new=config_path):
        with pytest.raises(ValueError, match="Invalid data level: l3bc"):
            scheduled_job_config_reader.read_scheduled_job_config()


@pytest.mark.parametrize(
    ("config_text", "schedule_expr"),
    [
        (
            "cron(20 6 * * ? * too many), swapi, l3b, sci\n",
            "cron(20 6 * * ? * too many)",
        ),
        ("cron(20 too few), swapi, l3b, sci\n", "cron(20 too few)"),
        ("hcron(20 too few), swapi, l3b, sci\n", "hcron(20 too few)"),
    ],
)
def test_read_scheduled_job_config_validates_schedule(
    config_text, schedule_expr, tmp_path
):
    """Tests config reader.

    Asserts that the config reader throws an
    exception with an invalid schedule expression.
    """
    config_path = tmp_path / "test_scheduled_jobs_config.csv"

    config_path.write_text(config_text, encoding="utf-8")

    with patch.object(scheduled_job_config_reader, "CONFIG_PATH", new=config_path):
        with pytest.raises(
            ValueError, match=re.escape(f"Invalid schedule expression: {schedule_expr}")
        ):
            scheduled_job_config_reader.read_scheduled_job_config()


def test_config_is_valid():
    """Tests the config reader.

    Will fail when there is an invalid config
    file.
    """
    assert isinstance(scheduled_job_config_reader.read_scheduled_job_config(), dict)
