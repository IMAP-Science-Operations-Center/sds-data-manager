"""Tests for the SPICE Query API."""

import json
from datetime import datetime, timedelta
import imap_data_access

from sds_data_manager.lambda_code.SDSCode.api_lambdas import spice_metakernel_api
from sds_data_manager.lambda_code.SDSCode.database import models

def _irrelevent_data():
    '''The metakernel code should not be looking into any of these fields.'''
    irrelevent_data = {
        "min_date_datetime": datetime.now(),
        "max_date_datetime": datetime.now(),
        "file_intervals_datetime": [["0", "0"]],
        "min_date_sclk": "",
        "max_date_sclk": "",
        "file_intervals_sclk": [["0", "0"]],
        "sclk_kernel": "nothing",
        "lsk_kernel": "nothing",
    }
    return irrelevent_data

def _insert_test_file(session, filename, intervals, upload_time=0):
    spice_object = imap_data_access.SPICEFilePath(filename)
    version = spice_object.spice_metadata['version']
    metadata_params = {
        "file_name": filename,
        "file_root": ''.join(filename.rsplit(version, 1)),
        "kernel_type": spice_object.spice_metadata['type'],
        "version": version,
        "file_intervals_j2000": intervals,
        "min_date_j2000": intervals[0][0],
        "max_date_j2000": intervals[-1][1],
        "ingestion_date": datetime.now()+timedelta(upload_time),
    } | _irrelevent_data()
    session.add(models.SPICEFiles(**metadata_params))
    session.commit()

def _insert_test_data(session):
    """Put a filepath into the test data."""
    # This file should NOT be loaded, because there is a
    # a newer version of the file
    _insert_test_file(session,
                      "imap_1000_001_1000_100_001.ah.bc",
                      [[1, 50],[55, 65], [75,100]])
    # This file should be loaded, because it is a high
    # priority file covering a large time range
    _insert_test_file(session,
                      "imap_1000_001_1000_100_002.ah.bc",
                      [[1, 50],[55, 65], [75,100]])
    # This file should be loaded, because it is the only file
    # covering 50-55
    _insert_test_file(session,
                      "imap_1000_001_1000_055_002.ap.bc",
                      [[1,55]])

    # This file should NOT be loaded, because there is a 
    # history file covering all of this data
    _insert_test_file(session,
                      "imap_1000_010_1000_020_002.ap.bc",
                      [[10,20]])
    
    # This file should NOT be loaded, because there is a 
    # history file covering all of this data
    _insert_test_file(session,
                      "imap_1000_090_1000_100_002.ap.bc",
                      [[90,100]])
    
    # This file should be loaded in, because it was uploaded 
    # AFTER the previous file, so it has a higher priority.
    _insert_test_file(session,
                      "imap_1000_060_1000_070_003.ap.bc",
                      [[60,70]],
                      upload_time=10)
    
    # This file should be loaded, because there has been no
    # data for time 65-75 so far
    _insert_test_file(session,
                      "imap_1000_065_1000_090_003.ap.bc",
                      [[65,90]],
                      upload_time=2)

    # This file should NOT be loaded, because the file just
    # before this one has a higher version number. Even
    # though this file was uploaded at a later date, version
    # always takes precidence. 
    _insert_test_file(session,
                      "imap_1000_065_1000_090_001.ap.bc",
                      [[65,90]],
                      upload_time=11)
    
    # This file should be loaded, because there has been no
    # data for time=0 so far
    _insert_test_file(session,
                      "imap_1000_001_1000_300_003.ap.bc",
                      [[1,300]],
                      upload_time=1)

def test_metakernel(session):
    """Tests that metakernel works as predicted."""
    _insert_test_data(session)
    result = spice_metakernel_api.lambda_handler(
        {
            "queryStringParameters": {
                "start_time": 1,
                "end_time": 100,
                "spice_path": '',
                "list_files": 'True'
            }
        },
        None,
    )

    '''
    This SPICE metakernel should have found the following files:
     
    1) imap_1000_001_1000_100_002.ah.bc - the best file to load in, because 
        it is a history file with a large amount of coverage in the interval
    
    There are now gaps between 50-55 and 65-75

    2) imap_1000_060_1000_070_003.ap.bc - The next best file to load in.
        It was uploaded recently, and covers 65-70.

    There are now gaps between 50-55 and 70-75

    3) imap_1000_065_1000_090_003.ap.bc - This file covers the 70-75 gap
        that remains. 
    
    There are now gaps between 50-55

    4) imap_1000_001_1000_300_003.ap.bc - This file covers everything 
        in the time range, but it was uploaded very early in the mission,
        so it gets chose to plug in the remaining gaps in the time range
    
    Ther are now no gaps remaining. 
    '''

    results = json.loads(result['body'])
    assert len(results) == 4
    assert results[0]['file_name'] == "ck/imap_1000_001_1000_300_003.ap.bc"
    assert results[1]['file_name'] == "ck/imap_1000_065_1000_090_003.ap.bc"
    assert results[2]['file_name'] == "ck/imap_1000_060_1000_070_003.ap.bc"
    assert results[3]['file_name'] == "ck/imap_1000_001_1000_100_002.ah.bc"
    
    '''
    If someone focuses the metakernel on a more specific time range, it should go straight
    to the appropriate file. 
    '''
    result = spice_metakernel_api.lambda_handler(
        {
            "queryStringParameters": {
                "start_time": 53,
                "end_time": 54,
                "spice_path": '',
                "list_files": 'True'
            }
        },
        None,
    )

    results = json.loads(result['body'])
    assert len(results) == 1
    assert results[0]['file_name'] == "ck/imap_1000_001_1000_300_003.ap.bc"
    
    result = spice_metakernel_api.lambda_handler(
        {
            "queryStringParameters": {
                "start_time": 20,
                "end_time": 25,
                "spice_path": '',
                "list_files": 'True'
            }
        },
        None,
    )
    
    results = json.loads(result['body'])
    assert len(results) == 1
    assert results[0]['file_name'] == "ck/imap_1000_001_1000_100_002.ah.bc"

    
    '''
    Query the gap that two spice files individually cover
    '''
    result = spice_metakernel_api.lambda_handler(
        {
            "queryStringParameters": {
                "start_time": 65,
                "end_time": 75,
                "spice_path": '',
                "list_files": 'True'
            }
        },
        None,
    )
    
    results = json.loads(result['body'])
    assert len(results) == 2
    assert results[0]['file_name'] == "ck/imap_1000_065_1000_090_003.ap.bc"
    assert results[1]['file_name'] == "ck/imap_1000_060_1000_070_003.ap.bc"