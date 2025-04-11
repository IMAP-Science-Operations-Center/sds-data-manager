"""Tests for the SPICE indexer lambda."""

import json
import os
from datetime import datetime

import spiceypy

from sds_data_manager.lambda_code.SDSCode.api_lambdas import (
    spice_metakernel_api,
    spice_query_api,
)
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas import spice_indexer


def put_local_file_in_bucket(s3_client, path_in_s3, path_local):
    """Put the a local file into a test bucket, and return a mock event notification.

    Parameters
    ----------
    s3_client
        The s3 client to use
    path_in_s3: str
        The path to place the file in S3
    path_local: str
        The local path of the file to upload

    Returns
    -------
    s3_event: dict
        A dictionary mocking the s3 put event notification

    """
    with open(path_local, "rb") as f:
        s3_client.put_object(
            Bucket="test-data-bucket",
            Key=path_in_s3,
            Body=f,
        )
    event = {
        "detail-type": "Object Created",
        "source": "aws.s3",
        "time": datetime.now().isoformat(),
        "detail": {
            "version": "0",
            "bucket": {"name": "test-data-bucket"},
            "object": {
                "key": (path_in_s3),
                "reason": "PutObject",
            },
        },
    }
    return event


def test_s3_spice_files(session, s3_client, events_client):
    """Test s3 event.

    The following test mimics a leapsecond kernel being placed on the SDS,
    followed by a spacecraft clock kernel, and then an attitude file.

    The files are located in the "test_spice_files" directory.

    """
    temp_path = os.getenv("EFS_SPICE_MOUNT_PATH")
    current_path = os.path.dirname(os.path.abspath(__file__))
    one_level_up = os.path.abspath(os.path.join(current_path, ".."))
    test_spice_data_dir = os.path.join(one_level_up, "test-data", "test_spice_files")

    # Insert leapsecond spice kernel
    leapsecond_event = put_local_file_in_bucket(
        s3_client,
        "spice/lsk/naif0012.tls",
        os.path.join(test_spice_data_dir, "naif0012.tls"),
    )
    spice_indexer.lambda_handler(leapsecond_event, None)

    # Insert spacecraft clock spice kernel
    clock_kernel_event = put_local_file_in_bucket(
        s3_client,
        "spice/sclk/imap_sclk_0012.tsc",
        os.path.join(test_spice_data_dir, "imap_sclk_0012.tsc"),
    )
    spice_indexer.lambda_handler(clock_kernel_event, None)

    # Insert a new attitude kernel
    attitude_kernel_event = put_local_file_in_bucket(
        s3_client,
        "spice/ck/imap_2025_118_2025_120_001.ah.bc",
        os.path.join(test_spice_data_dir, "imap_2025_118_2025_120_001.ah.bc"),
    )
    spice_indexer.lambda_handler(attitude_kernel_event, None)

    # Verify that the file was moved to the temp_path directory
    assert os.path.exists(temp_path + "/lsk/naif0012.tls")
    assert os.path.exists(temp_path + "/sclk/imap_sclk_0012.tsc")
    assert os.path.exists(temp_path + "/ck/imap_2025_118_2025_120_001.ah.bc")

    # Verify that the database was populated appropriately
    # NOTE: This is also testing the spice_query_api, to help ensure compatibility
    result = spice_query_api.lambda_handler(
        {"queryStringParameters": {"type": "attitude_history"}}, None
    )
    result = json.loads(result["body"])
    assert len(result) == 1
    assert result[0]["kernel_type"] == "attitude_history"
    assert result[0]["version"] == 1
    assert len(result[0]["file_intervals_datetime"]) == 2  # 1 significant gap detected
    print(result)
    result = spice_query_api.lambda_handler(
        {"queryStringParameters": {"type": "leapseconds"}}, None
    )
    result = json.loads(result["body"])
    assert len(result) == 1
    assert result[0]["kernel_type"] == "leapseconds"
    assert result[0]["version"] == 12
    assert len(result[0]["file_intervals_datetime"]) == 1  # Default time range
    print(result)
    result = spice_query_api.lambda_handler(
        {"queryStringParameters": {"type": "spacecraft_clock"}}, None
    )
    result = json.loads(result["body"])
    assert len(result) == 1
    assert result[0]["kernel_type"] == "spacecraft_clock"
    assert result[0]["version"] == 12
    assert len(result[0]["file_intervals_datetime"]) == 1  # Default time range
    print(result)

    # Checking metakernel API here as well!
    result = spice_metakernel_api.lambda_handler(
        {
            "queryStringParameters": {
                "start_time": 0,
                "end_time": 1000000000,
                "spice_path": temp_path,
            }
        },
        None,
    )

    # Ensure that the metakernels are actually valid by loading them in
    with open(temp_path + "/metakernel.tm", "w") as f:
        f.write(result["body"])
    spiceypy.kclear()
    assert spiceypy.ktotal("ALL") == 0
    spiceypy.furnsh(temp_path + "/metakernel.tm")
    assert spiceypy.ktotal("TEXT") == 2  # LSK and SCLK kernels
    assert spiceypy.ktotal("META") == 1  # One Metakernel
    assert spiceypy.ktotal("CK") == 1  # One CK file
