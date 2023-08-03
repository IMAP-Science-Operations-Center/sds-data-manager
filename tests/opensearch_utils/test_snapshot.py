from unittest.mock import MagicMock

import pytest
from freezegun import freeze_time

from sds_data_manager.lambda_code.SDSCode.opensearch_utils import snapshot


@pytest.fixture()
def _mock_boto_session(monkeypatch):
    """Mock the boto session for credentials"""
    mock_session = MagicMock()

    mock_credentials = MagicMock()
    mock_credentials.access_key = "mock_access_key"
    mock_credentials.secret_key = "mock_secret_key"
    mock_credentials.token = "mock_token"

    mock_session.get_credentials.return_value = mock_credentials

    monkeypatch.setattr("boto3.Session", MagicMock(return_value=mock_session))


@pytest.mark.usefixtures("_mock_boto_session")
def test_get_auth():
    """Test that get_auth correctly returns credentials"""
    ## Arrange ##
    region = "us-west-2"
    service = "es"
    true_awsauth = snapshot.AWS4Auth(
        "mock_access_key",
        "mock_secret_key",
        region,
        service,
        session_token="mock_token",
    )

    ## Act ##
    out_awsauth = snapshot.get_auth(region)

    ## Assert ##
    assert true_awsauth.access_id == out_awsauth.access_id
    assert true_awsauth.region == out_awsauth.region
    assert true_awsauth.service == out_awsauth.service
    assert true_awsauth.session_token == out_awsauth.session_token


@freeze_time("2023-08-01 12:58:30")
@pytest.mark.usefixtures("_mock_boto_session")
def test_run_backup_no_exceptions(requests_mock):
    """test that run_backup runs without exceptions"""
    ## Arrange ##
    host = (
        "search-sdsmetadatadomain-x6xubdgtaqvrdn72uvgybjoiiu.us-west-2.es.amazonaws.com"
    )
    region = "us-west-2"
    snapshot_repo_name = "snapshot-repo"
    snapshot_s3_bucket = "snapshot-bucket"
    snapshot_role_arn = "arn:aws:iam::012345678901:role/snapshot-role"

    repo_url = "https://" + host + "/_snapshot/snapshot-repo"
    snapshot_url = repo_url + "/opensearch_snapshot_2023-08-01-12:58:30"
    # request repo successful request
    requests_mock.put(repo_url, text="mocked PUT response", status_code=200)
    # take snapshot successful request
    requests_mock.put(snapshot_url, text="mocked PUT response", status_code=200)

    ## Act ##
    snapshot.run_backup(
        host, region, snapshot_repo_name, snapshot_s3_bucket, snapshot_role_arn
    )

    ## Assert ##
    # No return, but no exceptions should be raised


@freeze_time("2023-08-01 12:58:30")
@pytest.mark.usefixtures("_mock_boto_session")
def test_run_backup_repo_exception(requests_mock):
    """test that run_backup returns an error when the repository
    registration requests returns an error status"""
    ## Arrange ##
    host = "sdsmetadata.com"
    region = "us-west-2"
    snapshot_repo_name = "snapshot-repo"
    snapshot_s3_bucket = "snapshot-bucket"
    snapshot_role_arn = "arn:aws:iam::012345678901:role/snapshot-role"

    repo_url = "https://" + host + "/_snapshot/snapshot-repo"
    snapshot_url = repo_url + "/opensearch_snapshot_2023-08-01-12:58:30"
    # register repo failed request
    requests_mock.put(repo_url, text="mocked PUT response", status_code=400)
    # take snapshot successful request
    requests_mock.put(snapshot_url, text="mocked PUT response", status_code=200)

    ## Act / Assert ##
    with pytest.raises(RuntimeError) as e:
        snapshot.run_backup(
            host, region, snapshot_repo_name, snapshot_s3_bucket, snapshot_role_arn
        )
    assert str(e) == "<ExceptionInfo RuntimeError('400.mocked PUT response') tblen=2>"


@freeze_time("2023-08-01 12:58:30")
@pytest.mark.usefixtures("_mock_boto_session")
def test_run_backup_snapshot_exception(requests_mock):
    """test that run_backup returns an exception when the take snapshot request
    returns and error status."""
    ## Arrange ##
    host = "search-sdsmetadatadomain.es.amazonaws.com"
    region = "us-west-2"
    snapshot_repo_name = "snapshot-repo"
    snapshot_s3_bucket = "snapshot-bucket"
    snapshot_role_arn = "arn:aws:iam::012345678901:role/snapshot-role"

    repo_url = "https://" + host + "/_snapshot/snapshot-repo"
    snapshot_url = repo_url + "/opensearch_snapshot_2023-08-01-12:58:30"
    # register repo successful request
    requests_mock.put(repo_url, text="mocked PUT response", status_code=200)
    # take snapshot failed request
    requests_mock.put(snapshot_url, text="mocked PUT response", status_code=400)

    ## Act / Assert ##
    with pytest.raises(RuntimeError) as e:
        snapshot.run_backup(
            host, region, snapshot_repo_name, snapshot_s3_bucket, snapshot_role_arn
        )

    assert str(e) == "<ExceptionInfo RuntimeError('400.mocked PUT response') tblen=2>"
