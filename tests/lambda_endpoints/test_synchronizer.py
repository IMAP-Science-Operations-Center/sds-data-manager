"""Testing the database synchronizer."""

import datetime

from sds_data_manager.lambda_code.SDSCode.database import models, synchronizer


def cleanup_bucket(s3_client):
    """Remove all objects from the test bucket."""
    items = s3_client.list_objects_v2(Bucket="test-data-bucket")
    for item in items.get("Contents", []):
        s3_client.delete_object(Bucket="test-data-bucket", Key=item["Key"])


def test_synchronizer_extra_s3(session, s3_client):
    """An s3 file not in the database already, gets added as expected."""
    cleanup_bucket(s3_client)

    filepath = "imap/hit/l0/2025/11/imap_hit_l0_raw_20251107_v001.pkts"
    s3_client.put_object(Bucket="test-data-bucket", Key=filepath, Body=b"")

    with session.begin():
        nfiles = session.query(models.ScienceFiles).count()
    assert nfiles == 0

    synchronizer.lambda_handler(event={}, context={})

    with session.begin():
        files = session.query(models.ScienceFiles).all()
    assert len(files) == 1

    item = files[0]
    assert item.file_path == filepath
    assert item.instrument == "hit"
    assert item.data_level == "l0"
    assert item.descriptor == "raw"
    assert item.start_date == datetime.datetime(2025, 11, 7)
    assert item.version == "v001"
    assert item.extension == "pkts"


def test_synchronizer_extra_db(session, s3_client):
    """A database entry gets removed if it isn't in s3."""
    cleanup_bucket(s3_client)
    filepath = "imap/hit/l0/2025/11/imap_hit_l0_raw_20251107_v001.pkts"
    metadata_params = {
        "file_path": filepath,
        "instrument": "hit",
        "data_level": "l0",
        "descriptor": "raw",
        "start_date": datetime.datetime.strptime("20251107", "%Y%m%d"),
        "version": "v001",
        "extension": "pkts",
        "ingestion_date": datetime.datetime.strptime(
            "2025-11-07 10:13:12+00:00", "%Y-%m-%d %H:%M:%S%z"
        ),
    }

    # # Add data to the file catalog and return the session
    with session.begin():
        session.add(models.ScienceFiles(**metadata_params))

    with session.begin():
        nfiles = session.query(models.ScienceFiles).count()
    assert nfiles == 1

    synchronizer.lambda_handler(event={}, context={})

    with session.begin():
        nfiles = session.query(models.ScienceFiles).count()
    assert nfiles == 0
