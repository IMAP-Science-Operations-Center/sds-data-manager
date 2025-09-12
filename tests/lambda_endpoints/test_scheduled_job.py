"""Tests the scheduled job lambda."""

import datetime as dt
from unittest.mock import call, patch

from imap_data_access import ProcessingInputCollection, RepointInput

from sds_data_manager.lambda_code.SDSCode.database.models import RepointFiles
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas import (
    batch_starter,
)
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.scheduled_job import (
    lambda_handler,
)


@patch(
    "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.scheduled_job.read_scheduled_job_config"
)
def test_scheduled_processing_event(mock_read_scheduled_job_config, session):
    """Tests ``lambda_handler`` when invoked with a scheduled job event."""
    context = {"context": "sample_context"}

    mock_read_scheduled_job_config.return_value = {
        "cron(20 6 * * ? *)": [
            {
                "data_source": "glows",
                "data_type": "l3b",
                "descriptor": "ion-rate-profile",
            },
        ],
        "cron(22 6 * * ? *)": [
            {
                "data_source": "hi",
                "data_type": "l3",
                "descriptor": "h90-ena-h-sf-sp-full-hae-6deg-3mo",
            },
            {
                "data_source": "lo",
                "data_type": "l3",
                "descriptor": "ilo-ena-h-sf-sp-full-hae-6deg-3mo",
            },
        ],
    }

    expected_start_date = dt.datetime.now().strftime("%Y%m%d")

    with (
        patch.object(batch_starter, "BATCH_CLIENT") as mock_batch_client,
    ):
        # call twice to ensure we submit a job each time the event is triggered
        lambda_handler({"scheduled": "cron(20 6 * * ? *)"}, context)

        # Verify we submit the glows daily job
        assert mock_batch_client.submit_job.call_count == 1
        mock_batch_client.submit_job.assert_called_with(
            jobName="glows-l3b-ion-rate-profile-job-1",
            jobQueue="ProcessingJobQueue",
            jobDefinition="ProcessingJob-glows-l3",
            containerOverrides={
                "command": [
                    "--instrument",
                    "glows",
                    "--data-level",
                    "l3b",
                    "--descriptor",
                    "ion-rate-profile",
                    "--start-date",
                    expected_start_date,
                    "--version",
                    "v001",
                    "--dependency",
                    f"imap_glows_l3b_ion-rate-profile-4f53cda1_{expected_start_date}_v001.json",
                    "--upload-to-sdc",
                ]
            },
            retryStrategy=batch_starter.BATCH_JOB_RETRY_STRATEGY,
        )

        mock_batch_client.submit_job.reset_mock()

        lambda_handler({"scheduled": "cron(22 6 * * ? *)"}, context)
        # Verify we submit both sp maps daily jobs
        assert mock_batch_client.submit_job.call_count == 2
        mock_batch_client.submit_job.assert_has_calls(
            [
                call(
                    jobName="hi-l3-h90-ena-h-sf-sp-full-hae-6deg-3mo-job-2",
                    jobQueue="ProcessingJobQueue",
                    jobDefinition="ProcessingJob-hi-l3",
                    containerOverrides={
                        "command": [
                            "--instrument",
                            "hi",
                            "--data-level",
                            "l3",
                            "--descriptor",
                            "h90-ena-h-sf-sp-full-hae-6deg-3mo",
                            "--start-date",
                            expected_start_date,
                            "--version",
                            "v001",
                            "--dependency",
                            f"imap_hi_l3_h90-ena-h-sf-sp-full-hae-6deg-3mo-4f53cda1_{expected_start_date}_v001.json",
                            "--upload-to-sdc",
                        ]
                    },
                    retryStrategy=batch_starter.BATCH_JOB_RETRY_STRATEGY,
                ),
                call(
                    jobName="lo-l3-ilo-ena-h-sf-sp-full-hae-6deg-3mo-job-3",
                    jobQueue="ProcessingJobQueue",
                    jobDefinition="ProcessingJob-lo-l3",
                    containerOverrides={
                        "command": [
                            "--instrument",
                            "lo",
                            "--data-level",
                            "l3",
                            "--descriptor",
                            "ilo-ena-h-sf-sp-full-hae-6deg-3mo",
                            "--start-date",
                            expected_start_date,
                            "--version",
                            "v001",
                            "--dependency",
                            f"imap_lo_l3_ilo-ena-h-sf-sp-full-hae-6deg-3mo-4f53cda1_{expected_start_date}_v001.json",
                            "--upload-to-sdc",
                        ]
                    },
                    retryStrategy=batch_starter.BATCH_JOB_RETRY_STRATEGY,
                ),
            ]
        )


@patch(
    "sds_data_manager.lambda_code.SDSCode.pipeline_lambdas.scheduled_job.read_scheduled_job_config"
)
def test_scheduled_job_passes_repointing(mock_read_job_config, session):
    """Tests ``lambda_handler`` when invoked with a scheduled job event."""
    context = {"context": "sample_context"}

    glows_job = {
        "data_source": "glows",
        "data_type": "l3b",
        "descriptor": "ion-rate-profile",
    }

    mock_read_job_config.return_value = {"cron(21 6 * * ? *)": [glows_job]}

    yesterdays_date = dt.datetime.now() - dt.timedelta(days=1)

    year_day_formatted_date = yesterdays_date.strftime("%Y_%j")
    repointing_file_name = f"imap_{year_day_formatted_date}_01.repoint.csv"

    records = [
        RepointFiles(
            file_path=f"imap/spice/repoint/{repointing_file_name}",
            end_date=yesterdays_date,
            version="01",
            ingestion_date=dt.datetime.now(),
        )
    ]
    session.add_all(records)
    session.commit()

    expected_start_date = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")

    with (
        patch.object(batch_starter, "try_to_submit_job") as mock_submit,
    ):
        repoint_input = RepointInput(repointing_file_name)
        expected_processing_input = ProcessingInputCollection(repoint_input)
        events = {"scheduled": "cron(21 6 * * ? *)", "version": 5}
        lambda_handler(events, context)

        assert 1 == mock_submit.call_count
        [sess, job, start_date, version, deps] = mock_submit.call_args_list[0].args

        assert session == sess
        assert glows_job == job
        assert expected_start_date == start_date
        assert "v005" == version
        assert expected_processing_input.serialize() == deps
