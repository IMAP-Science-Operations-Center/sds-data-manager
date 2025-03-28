import os
from datetime import datetime

import pytest
from imap_data_access import SPICEFilePath
from sqlalchemy import select
from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas import spice_indexer

def test_s3_sci_event(session, s3_client, events_client):
    """Test s3 event."""
    current_path = os.path.dirname(os.path.abspath(__file__))
    filepath = "imap/spice/ck/imap_2025_118_2025_120_001.ah.bc"
    with open(current_path +"/test_spice_files/imap_2025_118_2025_120_001.ah.bc", 'rb') as f:
        s3_client.put_object(
            Bucket="test-data-bucket",
            Key=filepath,
            Body=f,
        )
    event = {
        "detail-type": "Object Created",
        "source": "aws.s3",
        "time": "2024-01-16T17:35:08Z",
        "detail": {
            "version": "0",
            "bucket": {"name": "test-data-bucket"},
            "object": {
                "key": (filepath),
                "reason": "PutObject",
            },
        },
    }

    import shutil
    source_path = current_path+"/test_spice_files/imapsclk_0012.tsc"
    destination_path = "/tmp/sclk/imapsclk_0012.tsc"
    if not os.path.exists("/tmp/sclk"):
        os.mkdir("/tmp/sclk")
    if not os.path.exists(destination_path):
        shutil.copy(source_path, destination_path)
    
    source_path = current_path+"/test_spice_files/naif0012.tls"
    destination_path = "/tmp/lsk/naif0012.tls"
    if not os.path.exists("/tmp/lsk"):
        os.mkdir("/tmp/lsk")
    if not os.path.exists(destination_path):
        shutil.copy(source_path, destination_path)
    
    spice_indexer.lambda_handler(event, None)