import os
from datetime import datetime

import pytest
from imap_data_access import SPICEFilePath
from sqlalchemy import select
from sds_data_manager.lambda_code.SDSCode.database import models
from sds_data_manager.lambda_code.SDSCode.pipeline_lambdas import spice_indexer

def put_local_file_in_bucket(s3_client, path_in_s3, path_local):
    with open(path_local, 'rb') as f:
        s3_client.put_object(
            Bucket="test-data-bucket",
            Key=path_in_s3,
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
                "key": (path_in_s3),
                "reason": "PutObject",
            },
        },
    }
    return event

def test_s3_spice_files(session, s3_client, events_client):
    """Test s3 event."""
    current_path = os.path.dirname(os.path.abspath(__file__))

    leapsecond_event = put_local_file_in_bucket(s3_client, "imap/spice/lsk/naif0012.tls", current_path+"/test_spice_files/naif0012.tls")
    spice_indexer.lambda_handler(leapsecond_event, None)

    clock_kernel_event = put_local_file_in_bucket(s3_client, "imap/spice/sclk/imapsclk_0012.tsc", current_path+"/test_spice_files/imapsclk_0012.tsc")
    spice_indexer.lambda_handler(clock_kernel_event , None)

    attitude_kernel_event = put_local_file_in_bucket(s3_client, "imap/spice/ck/imap_2025_118_2025_120_001.ah.bc", current_path +"/test_spice_files/imap_2025_118_2025_120_001.ah.bc")
    spice_indexer.lambda_handler(attitude_kernel_event, None)

    
    # Verify that the file was moved
    assert os.path.exists("/tmp/imap/spice/lsk/naif0012.tls")
    assert os.path.exists("/tmp/imap/spice/sclk/imapsclk_0012.tsc")
    assert os.path.exists("/tmp/imap/ck/imap_2025_118_2025_120_001.ah.bc")

    # Verify that the database was populated appropriately
    result = session.query(models.SPICEFiles).all()
    for r in result:
        if r.file_path == "/tmp/imap/ck/imap_2025_118_2025_120_001.ah.bc":
            assert r.kernel_type == "attitude_history"
            assert r.version==1
            assert len(r.file_intervals_datetime) == 2 #1 significant gap detected
        print(r.file_intervals_datetime)
'''
    file_path = Column(String, nullable=False, primary_key=True, unique=True)
    ingestion_date = Column(DateTime(timezone=True))
    file_root = Column(String)
    kernel_type = Column(String)
    min_date_j2000 = Column(Float)
    max_date_j2000 = Column(Float)
    file_intervals_j2000 = Column(JSON)
    min_date_datetime = Column(DateTime(timezone=True))
    max_date_datetime = Column(DateTime(timezone=True))
    file_intervals_datetime = Column(JSON)
    min_date_sclk = Column(String)
    max_date_sclk = Column(String)
    file_intervals_sclk = Column(JSON)
    sclk_kernel = Column(String)
    lsk_kernel = Column(String)
    version = Column(Integer, nullable=True)'
    '''