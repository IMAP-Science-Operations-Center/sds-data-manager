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


def test_ancillary(session, s3_client):
    """An s3 file not in the database already, gets added as expected."""
    cleanup_bucket(s3_client)

    filepath = "imap/ancillary/glows/imap_glows_l3b-archive-zip_20100326_v012.cdf"
    s3_client.put_object(Bucket="test-data-bucket", Key=filepath, Body=b"")

    with session.begin():
        nfiles = session.query(models.ScienceFiles).count()
    assert nfiles == 0

    synchronizer.lambda_handler(event={}, context={})

    with session.begin():
        files = session.query(models.AncillaryFiles).all()
    assert len(files) == 1

    item = files[0]
    assert item.file_path == filepath
    assert item.instrument == "glows"
    assert item.descriptor == "l3b-archive-zip"
    assert item.start_date == datetime.datetime(2010, 3, 26)
    assert item.version == "v012"
    assert item.extension == "cdf"


def test_synchronizer_spice_file_added(session, s3_client):
    """A SPICE file in S3 but not in the database gets added as expected."""
    cleanup_bucket(s3_client)

    filepath = "imap/spice/lsk/naif0012.tls"
    s3_client.put_object(Bucket="test-data-bucket", Key=filepath, Body=b"")

    with session.begin():
        nfiles = session.query(models.SPICEFiles).count()
    assert nfiles == 0

    synchronizer.lambda_handler(event={}, context={})

    with session.begin():
        files = session.query(models.SPICEFiles).all()
    assert len(files) == 1

    item = files[0]
    assert item.file_path == filepath
    assert item.file_name == "naif0012.tls"
    assert item.kernel_type == "leapseconds"
    assert item.version == 12
    assert item.file_root == "naif.tls"


def test_synchronizer_spice_file_removed(session, s3_client):
    """A SPICE database entry gets removed if it isn't in S3."""
    cleanup_bucket(s3_client)
    filepath = "imap/spice/lsk/naif0012.tls"
    metadata_params = {
        "file_path": filepath,
        "file_name": "naif0012.tls",
        "ingestion_date": datetime.datetime.strptime(
            "2025-01-01 10:00:00+00:00", "%Y-%m-%d %H:%M:%S%z"
        ),
        "file_root": "naif.tls",
        "kernel_type": "leapseconds",
        "version": 12,
    }

    # Add data to the file catalog and return the session
    with session.begin():
        session.add(models.SPICEFiles(**metadata_params))

    with session.begin():
        nfiles = session.query(models.SPICEFiles).count()
    assert nfiles == 1

    synchronizer.lambda_handler(event={}, context={})

    with session.begin():
        nfiles = session.query(models.SPICEFiles).count()
    assert nfiles == 0


def test_synchronizer_spin_file_added(session, s3_client):
    """A spin file in S3 but not in the database gets added as expected."""
    cleanup_bucket(s3_client)

    filepath = "imap/spice/spin/imap_2026_267_2026_267_01.spin.csv"
    s3_client.put_object(Bucket="test-data-bucket", Key=filepath, Body=b"")

    with session.begin():
        nfiles = session.query(models.SpinFiles).count()
    assert nfiles == 0

    synchronizer.lambda_handler(event={}, context={})

    with session.begin():
        files = session.query(models.SpinFiles).all()
    assert len(files) == 1

    item = files[0]
    assert item.file_path == filepath
    assert item.start_date == datetime.datetime(2026, 9, 24)
    assert item.end_date == datetime.datetime(2026, 9, 24)
    assert item.version == "01"


def test_synchronizer_spin_file_removed(session, s3_client):
    """A spin database entry gets removed if it isn't in S3."""
    cleanup_bucket(s3_client)
    filepath = "imap/spice/spin/imap_2026_267_2026_267_01.spin.csv"
    metadata_params = {
        "file_path": filepath,
        "start_date": datetime.datetime(2026, 9, 24),
        "end_date": datetime.datetime(2026, 9, 24),
        "version": "01",
        "ingestion_date": datetime.datetime.strptime(
            "2025-01-01 10:00:00+00:00", "%Y-%m-%d %H:%M:%S%z"
        ),
    }

    with session.begin():
        session.add(models.SpinFiles(**metadata_params))

    with session.begin():
        nfiles = session.query(models.SpinFiles).count()
    assert nfiles == 1

    synchronizer.lambda_handler(event={}, context={})

    with session.begin():
        nfiles = session.query(models.SpinFiles).count()
    assert nfiles == 0


def test_synchronizer_small_forces_file_added(session, s3_client):
    """A small forces file in S3 but not in the database gets added as expected."""
    cleanup_bucket(s3_client)

    filepath = "imap/spice/activities/imap_2025_100_2025_110_hist_01.sff"
    s3_client.put_object(Bucket="test-data-bucket", Key=filepath, Body=b"")

    with session.begin():
        nfiles = session.query(models.SmallForcesFile).count()
    assert nfiles == 0

    synchronizer.lambda_handler(event={}, context={})

    with session.begin():
        files = session.query(models.SmallForcesFile).all()
    assert len(files) == 1

    item = files[0]
    assert item.file_path == filepath
    assert item.start_date == datetime.datetime(2025, 4, 10)
    assert item.end_date == datetime.datetime(2025, 4, 20)
    assert item.version == "01"


def test_synchronizer_small_forces_file_removed(session, s3_client):
    """A small forces database entry gets removed if it isn't in S3."""
    cleanup_bucket(s3_client)
    filepath = "imap/spice/activities/imap_2025_100_2025_110_hist_01.sff"
    metadata_params = {
        "file_path": filepath,
        "start_date": datetime.datetime(2025, 4, 10),
        "end_date": datetime.datetime(2025, 4, 20),
        "version": "01",
        "ingestion_date": datetime.datetime.strptime(
            "2025-01-01 10:00:00+00:00", "%Y-%m-%d %H:%M:%S%z"
        ),
    }

    with session.begin():
        session.add(models.SmallForcesFile(**metadata_params))

    with session.begin():
        nfiles = session.query(models.SmallForcesFile).count()
    assert nfiles == 1

    synchronizer.lambda_handler(event={}, context={})

    with session.begin():
        nfiles = session.query(models.SmallForcesFile).count()
    assert nfiles == 0
