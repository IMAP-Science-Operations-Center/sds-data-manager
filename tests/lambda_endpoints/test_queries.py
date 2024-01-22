"""Test queries lambda"""
import datetime
import json

import pytest
from sqlalchemy.orm import Session

from sds_data_manager.lambda_code.SDSCode import queries
from sds_data_manager.lambda_code.SDSCode.database import models


@pytest.fixture()
def setup_test_data(test_engine):
    metadata_params = {
        "file_path": "test/file/path/imap_hit_l0_raw_20251107_20251108_v02-01.pkts",
        "instrument": "hit",
        "data_level": "l0",
        "descriptor": "raw",
        "start_date": datetime.datetime.strptime("20251107", "%Y%m%d"),
        "end_date": datetime.datetime.strptime("20251108", "%Y%m%d"),
        "version": "v02-01",
        "extension": "pkts",
    }

    # Add data to the file catalog
    with Session(test_engine) as session:
        session.add(models.FileCatalog(**metadata_params))
        session.commit()

    return ""


@pytest.fixture()
def expected_response():
    expected_response = json.dumps(
        str(
            [
                (
                    1,
                    "test/file/path/imap_hit_l0_raw_20251107_20251108_v02-01.pkts",
                    "hit",
                    "l0",
                    "raw",
                    datetime.datetime(2025, 11, 7, 0, 0),
                    datetime.datetime(2025, 11, 8, 0, 0),
                    "v02-01",
                    "pkts",
                    None,
                )
            ]
        )
    )
    return expected_response


def test_start_date_query(setup_test_data, test_engine, expected_response):
    """Test that start date can be queried"""
    event = {
        "resource": "/query",
        "path": "/query",
        "httpMethod": "GET",
        "headers": {
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br",
            "Host": "qnpkwgob0f.execute-api.us-west-2.amazonaws.com",
            "User-Agent": "python-requests/2.27.1",
            "X-Amzn-Trace-Id": "Root=1-65aafc00-466eb23e1510d4901018197a",
            "X-Forwarded-For": "128.138.131.13",
            "X-Forwarded-Port": "443",
            "X-Forwarded-Proto": "https",
        },
        "multiValueHeaders": {
            "Accept": ["*/*"],
            "Accept-Encoding": ["gzip, deflate, br"],
            "Host": ["qnpkwgob0f.execute-api.us-west-2.amazonaws.com"],
            "User-Agent": ["python-requests/2.27.1"],
            "X-Amzn-Trace-Id": ["Root=1-65aafc00-466eb23e1510d4901018197a"],
            "X-Forwarded-For": ["128.138.131.13"],
            "X-Forwarded-Port": ["443"],
            "X-Forwarded-Proto": ["https"],
        },
        "queryStringParameters": {"start_date": "20251101"},
        "multiValueQueryStringParameters": {
            "end_date": ["20271209"],
            "start_date": ["20251101"],
        },
        "pathParameters": None,
        "stageVariables": None,
        "requestContext": {
            "resourceId": "61kave",
            "resourcePath": "/query",
            "httpMethod": "GET",
            "extendedRequestId": "RzxQEHVvvHcEVrw=",
            "requestTime": "19/Jan/2024:22:47:28 +0000",
            "path": "/prod//query",
            "accountId": "021076979309",
            "protocol": "HTTP/1.1",
            "stage": "prod",
            "domainPrefix": "qnpkwgob0f",
            "requestTimeEpoch": 1705704448166,
            "requestId": "e96e6d69-5a0b-4b56-a25b-7d510c1a7fc2",
            "identity": {
                "cognitoIdentityPoolId": None,
                "accountId": None,
                "cognitoIdentityId": None,
                "caller": None,
                "sourceIp": "128.138.131.13",
                "principalOrgId": None,
                "accessKey": None,
                "cognitoAuthenticationType": None,
                "cognitoAuthenticationProvider": None,
                "userArn": None,
                "userAgent": "python-requests/2.27.1",
                "user": None,
            },
            "domainName": "qnpkwgob0f.execute-api.us-west-2.amazonaws.com",
            "apiId": "qnpkwgob0f",
        },
        "body": None,
        "isBase64Encoded": False,
    }

    returned_query = queries.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    assert returned_query["body"] == expected_response


def test_end_date_query(setup_test_data, test_engine, expected_response):
    """Test that end date can be queried"""
    event = {
        "resource": "/query",
        "path": "/query",
        "httpMethod": "GET",
        "headers": {
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br",
            "Host": "qnpkwgob0f.execute-api.us-west-2.amazonaws.com",
            "User-Agent": "python-requests/2.27.1",
            "X-Amzn-Trace-Id": "Root=1-65aafc00-466eb23e1510d4901018197a",
            "X-Forwarded-For": "128.138.131.13",
            "X-Forwarded-Port": "443",
            "X-Forwarded-Proto": "https",
        },
        "multiValueHeaders": {
            "Accept": ["*/*"],
            "Accept-Encoding": ["gzip, deflate, br"],
            "Host": ["qnpkwgob0f.execute-api.us-west-2.amazonaws.com"],
            "User-Agent": ["python-requests/2.27.1"],
            "X-Amzn-Trace-Id": ["Root=1-65aafc00-466eb23e1510d4901018197a"],
            "X-Forwarded-For": ["128.138.131.13"],
            "X-Forwarded-Port": ["443"],
            "X-Forwarded-Proto": ["https"],
        },
        "queryStringParameters": {"start_date": "20251101"},
        "multiValueQueryStringParameters": {},
        "pathParameters": None,
        "stageVariables": None,
        "requestContext": {
            "resourceId": "61kave",
            "resourcePath": "/query",
            "httpMethod": "GET",
            "extendedRequestId": "RzxQEHVvvHcEVrw=",
            "requestTime": "19/Jan/2024:22:47:28 +0000",
            "path": "/prod//query",
            "accountId": "021076979309",
            "protocol": "HTTP/1.1",
            "stage": "prod",
            "domainPrefix": "qnpkwgob0f",
            "requestTimeEpoch": 1705704448166,
            "requestId": "e96e6d69-5a0b-4b56-a25b-7d510c1a7fc2",
            "identity": {
                "cognitoIdentityPoolId": None,
                "accountId": None,
                "cognitoIdentityId": None,
                "caller": None,
                "sourceIp": "128.138.131.13",
                "principalOrgId": None,
                "accessKey": None,
                "cognitoAuthenticationType": None,
                "cognitoAuthenticationProvider": None,
                "userArn": None,
                "userAgent": "python-requests/2.27.1",
                "user": None,
            },
            "domainName": "qnpkwgob0f.execute-api.us-west-2.amazonaws.com",
            "apiId": "qnpkwgob0f",
        },
        "body": None,
        "isBase64Encoded": False,
    }
    returned_query = queries.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    assert returned_query["body"] == expected_response


def test_start_and_end_date_query(setup_test_data, test_engine, expected_response):
    "test that both start and end date can be queried"
    event = {
        "resource": "/query",
        "path": "/query",
        "httpMethod": "GET",
        "headers": {
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br",
            "Host": "qnpkwgob0f.execute-api.us-west-2.amazonaws.com",
            "User-Agent": "python-requests/2.27.1",
            "X-Amzn-Trace-Id": "Root=1-65aafc00-466eb23e1510d4901018197a",
            "X-Forwarded-For": "128.138.131.13",
            "X-Forwarded-Port": "443",
            "X-Forwarded-Proto": "https",
        },
        "multiValueHeaders": {
            "Accept": ["*/*"],
            "Accept-Encoding": ["gzip, deflate, br"],
            "Host": ["qnpkwgob0f.execute-api.us-west-2.amazonaws.com"],
            "User-Agent": ["python-requests/2.27.1"],
            "X-Amzn-Trace-Id": ["Root=1-65aafc00-466eb23e1510d4901018197a"],
            "X-Forwarded-For": ["128.138.131.13"],
            "X-Forwarded-Port": ["443"],
            "X-Forwarded-Proto": ["https"],
        },
        "queryStringParameters": {"start_date": "20251101", "end_date": "20251201"},
        "multiValueQueryStringParameters": {},
        "pathParameters": None,
        "stageVariables": None,
        "requestContext": {
            "resourceId": "61kave",
            "resourcePath": "/query",
            "httpMethod": "GET",
            "extendedRequestId": "RzxQEHVvvHcEVrw=",
            "requestTime": "19/Jan/2024:22:47:28 +0000",
            "path": "/prod//query",
            "accountId": "021076979309",
            "protocol": "HTTP/1.1",
            "stage": "prod",
            "domainPrefix": "qnpkwgob0f",
            "requestTimeEpoch": 1705704448166,
            "requestId": "e96e6d69-5a0b-4b56-a25b-7d510c1a7fc2",
            "identity": {
                "cognitoIdentityPoolId": None,
                "accountId": None,
                "cognitoIdentityId": None,
                "caller": None,
                "sourceIp": "128.138.131.13",
                "principalOrgId": None,
                "accessKey": None,
                "cognitoAuthenticationType": None,
                "cognitoAuthenticationProvider": None,
                "userArn": None,
                "userAgent": "python-requests/2.27.1",
                "user": None,
            },
            "domainName": "qnpkwgob0f.execute-api.us-west-2.amazonaws.com",
            "apiId": "qnpkwgob0f",
        },
        "body": None,
        "isBase64Encoded": False,
    }

    returned_query = queries.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    assert returned_query["body"] == expected_response


def test_empty_start_date_query(setup_test_data, test_engine, expected_response):
    "Test that a start_date query with no matches returns an empty list"
    event = {
        "resource": "/query",
        "path": "/query",
        "httpMethod": "GET",
        "headers": {
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br",
            "Host": "qnpkwgob0f.execute-api.us-west-2.amazonaws.com",
            "User-Agent": "python-requests/2.27.1",
            "X-Amzn-Trace-Id": "Root=1-65aafc00-466eb23e1510d4901018197a",
            "X-Forwarded-For": "128.138.131.13",
            "X-Forwarded-Port": "443",
            "X-Forwarded-Proto": "https",
        },
        "multiValueHeaders": {
            "Accept": ["*/*"],
            "Accept-Encoding": ["gzip, deflate, br"],
            "Host": ["qnpkwgob0f.execute-api.us-west-2.amazonaws.com"],
            "User-Agent": ["python-requests/2.27.1"],
            "X-Amzn-Trace-Id": ["Root=1-65aafc00-466eb23e1510d4901018197a"],
            "X-Forwarded-For": ["128.138.131.13"],
            "X-Forwarded-Port": ["443"],
            "X-Forwarded-Proto": ["https"],
        },
        "queryStringParameters": {"start_date": "20261101"},
        "multiValueQueryStringParameters": {
            "end_date": ["20271209"],
            "start_date": ["20251101"],
        },
        "pathParameters": None,
        "stageVariables": None,
        "requestContext": {
            "resourceId": "61kave",
            "resourcePath": "/query",
            "httpMethod": "GET",
            "extendedRequestId": "RzxQEHVvvHcEVrw=",
            "requestTime": "19/Jan/2024:22:47:28 +0000",
            "path": "/prod//query",
            "accountId": "021076979309",
            "protocol": "HTTP/1.1",
            "stage": "prod",
            "domainPrefix": "qnpkwgob0f",
            "requestTimeEpoch": 1705704448166,
            "requestId": "e96e6d69-5a0b-4b56-a25b-7d510c1a7fc2",
            "identity": {
                "cognitoIdentityPoolId": None,
                "accountId": None,
                "cognitoIdentityId": None,
                "caller": None,
                "sourceIp": "128.138.131.13",
                "principalOrgId": None,
                "accessKey": None,
                "cognitoAuthenticationType": None,
                "cognitoAuthenticationProvider": None,
                "userArn": None,
                "userAgent": "python-requests/2.27.1",
                "user": None,
            },
            "domainName": "qnpkwgob0f.execute-api.us-west-2.amazonaws.com",
            "apiId": "qnpkwgob0f",
        },
        "body": None,
        "isBase64Encoded": False,
    }
    expected_response = json.dumps("[]")
    returned_query = queries.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    assert returned_query["body"] == expected_response


def test_empty_end_date_query(setup_test_data, test_engine):
    "Test that an end_date query with no matches returns an empty list"
    event = {
        "resource": "/query",
        "path": "/query",
        "httpMethod": "GET",
        "headers": {
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br",
            "Host": "qnpkwgob0f.execute-api.us-west-2.amazonaws.com",
            "User-Agent": "python-requests/2.27.1",
            "X-Amzn-Trace-Id": "Root=1-65aafc00-466eb23e1510d4901018197a",
            "X-Forwarded-For": "128.138.131.13",
            "X-Forwarded-Port": "443",
            "X-Forwarded-Proto": "https",
        },
        "multiValueHeaders": {
            "Accept": ["*/*"],
            "Accept-Encoding": ["gzip, deflate, br"],
            "Host": ["qnpkwgob0f.execute-api.us-west-2.amazonaws.com"],
            "User-Agent": ["python-requests/2.27.1"],
            "X-Amzn-Trace-Id": ["Root=1-65aafc00-466eb23e1510d4901018197a"],
            "X-Forwarded-For": ["128.138.131.13"],
            "X-Forwarded-Port": ["443"],
            "X-Forwarded-Proto": ["https"],
        },
        "queryStringParameters": {"start_date": "20261101"},
        "multiValueQueryStringParameters": {
            "end_date": ["20271209"],
            "start_date": ["20251101"],
        },
        "pathParameters": None,
        "stageVariables": None,
        "requestContext": {
            "resourceId": "61kave",
            "resourcePath": "/query",
            "httpMethod": "GET",
            "extendedRequestId": "RzxQEHVvvHcEVrw=",
            "requestTime": "19/Jan/2024:22:47:28 +0000",
            "path": "/prod//query",
            "accountId": "021076979309",
            "protocol": "HTTP/1.1",
            "stage": "prod",
            "domainPrefix": "qnpkwgob0f",
            "requestTimeEpoch": 1705704448166,
            "requestId": "e96e6d69-5a0b-4b56-a25b-7d510c1a7fc2",
            "identity": {
                "cognitoIdentityPoolId": None,
                "accountId": None,
                "cognitoIdentityId": None,
                "caller": None,
                "sourceIp": "128.138.131.13",
                "principalOrgId": None,
                "accessKey": None,
                "cognitoAuthenticationType": None,
                "cognitoAuthenticationProvider": None,
                "userArn": None,
                "userAgent": "python-requests/2.27.1",
                "user": None,
            },
            "domainName": "qnpkwgob0f.execute-api.us-west-2.amazonaws.com",
            "apiId": "qnpkwgob0f",
        },
        "body": None,
        "isBase64Encoded": False,
    }
    expected_response = json.dumps("[]")
    returned_query = queries.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    assert returned_query["body"] == expected_response


def test_empty_non_date_query(setup_test_data, test_engine):
    "Test that a non-date query with no matches returns an empty list"
    event = {
        "resource": "/query",
        "path": "/query",
        "httpMethod": "GET",
        "headers": {
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br",
            "Host": "qnpkwgob0f.execute-api.us-west-2.amazonaws.com",
            "User-Agent": "python-requests/2.27.1",
            "X-Amzn-Trace-Id": "Root=1-65aafc00-466eb23e1510d4901018197a",
            "X-Forwarded-For": "128.138.131.13",
            "X-Forwarded-Port": "443",
            "X-Forwarded-Proto": "https",
        },
        "multiValueHeaders": {
            "Accept": ["*/*"],
            "Accept-Encoding": ["gzip, deflate, br"],
            "Host": ["qnpkwgob0f.execute-api.us-west-2.amazonaws.com"],
            "User-Agent": ["python-requests/2.27.1"],
            "X-Amzn-Trace-Id": ["Root=1-65aafc00-466eb23e1510d4901018197a"],
            "X-Forwarded-For": ["128.138.131.13"],
            "X-Forwarded-Port": ["443"],
            "X-Forwarded-Proto": ["https"],
        },
        "queryStringParameters": {"data_level": "l2"},
        "multiValueQueryStringParameters": {
            "end_date": ["20271209"],
            "start_date": ["20251101"],
        },
        "pathParameters": None,
        "stageVariables": None,
        "requestContext": {
            "resourceId": "61kave",
            "resourcePath": "/query",
            "httpMethod": "GET",
            "extendedRequestId": "RzxQEHVvvHcEVrw=",
            "requestTime": "19/Jan/2024:22:47:28 +0000",
            "path": "/prod//query",
            "accountId": "021076979309",
            "protocol": "HTTP/1.1",
            "stage": "prod",
            "domainPrefix": "qnpkwgob0f",
            "requestTimeEpoch": 1705704448166,
            "requestId": "e96e6d69-5a0b-4b56-a25b-7d510c1a7fc2",
            "identity": {
                "cognitoIdentityPoolId": None,
                "accountId": None,
                "cognitoIdentityId": None,
                "caller": None,
                "sourceIp": "128.138.131.13",
                "principalOrgId": None,
                "accessKey": None,
                "cognitoAuthenticationType": None,
                "cognitoAuthenticationProvider": None,
                "userArn": None,
                "userAgent": "python-requests/2.27.1",
                "user": None,
            },
            "domainName": "qnpkwgob0f.execute-api.us-west-2.amazonaws.com",
            "apiId": "qnpkwgob0f",
        },
        "body": None,
        "isBase64Encoded": False,
    }
    expected_response = json.dumps("[]")
    returned_query = queries.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    assert returned_query["body"] == expected_response


def test_non_date_query(setup_test_data, test_engine, expected_response):
    """Test that a non-date parameters can be queried"""
    event = {
        "resource": "/query",
        "path": "/query",
        "httpMethod": "GET",
        "headers": {
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br",
            "Host": "qnpkwgob0f.execute-api.us-west-2.amazonaws.com",
            "User-Agent": "python-requests/2.27.1",
            "X-Amzn-Trace-Id": "Root=1-65aafc00-466eb23e1510d4901018197a",
            "X-Forwarded-For": "128.138.131.13",
            "X-Forwarded-Port": "443",
            "X-Forwarded-Proto": "https",
        },
        "multiValueHeaders": {
            "Accept": ["*/*"],
            "Accept-Encoding": ["gzip, deflate, br"],
            "Host": ["qnpkwgob0f.execute-api.us-west-2.amazonaws.com"],
            "User-Agent": ["python-requests/2.27.1"],
            "X-Amzn-Trace-Id": ["Root=1-65aafc00-466eb23e1510d4901018197a"],
            "X-Forwarded-For": ["128.138.131.13"],
            "X-Forwarded-Port": ["443"],
            "X-Forwarded-Proto": ["https"],
        },
        "queryStringParameters": {"instrument": "hit"},
        "multiValueQueryStringParameters": {
            "end_date": ["20271209"],
            "start_date": ["20251101"],
        },
        "pathParameters": None,
        "stageVariables": None,
        "requestContext": {
            "resourceId": "61kave",
            "resourcePath": "/query",
            "httpMethod": "GET",
            "extendedRequestId": "RzxQEHVvvHcEVrw=",
            "requestTime": "19/Jan/2024:22:47:28 +0000",
            "path": "/prod//query",
            "accountId": "021076979309",
            "protocol": "HTTP/1.1",
            "stage": "prod",
            "domainPrefix": "qnpkwgob0f",
            "requestTimeEpoch": 1705704448166,
            "requestId": "e96e6d69-5a0b-4b56-a25b-7d510c1a7fc2",
            "identity": {
                "cognitoIdentityPoolId": None,
                "accountId": None,
                "cognitoIdentityId": None,
                "caller": None,
                "sourceIp": "128.138.131.13",
                "principalOrgId": None,
                "accessKey": None,
                "cognitoAuthenticationType": None,
                "cognitoAuthenticationProvider": None,
                "userArn": None,
                "userAgent": "python-requests/2.27.1",
                "user": None,
            },
            "domainName": "qnpkwgob0f.execute-api.us-west-2.amazonaws.com",
            "apiId": "qnpkwgob0f",
        },
        "body": None,
        "isBase64Encoded": False,
    }

    returned_query = queries.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    assert returned_query["body"] == expected_response


def test_multi_param_query(setup_test_data, test_engine, expected_response):
    "Test that multiple parameters can be queried"
    event = {
        "resource": "/query",
        "path": "/query",
        "httpMethod": "GET",
        "headers": {
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br",
            "Host": "qnpkwgob0f.execute-api.us-west-2.amazonaws.com",
            "User-Agent": "python-requests/2.27.1",
            "X-Amzn-Trace-Id": "Root=1-65aafc00-466eb23e1510d4901018197a",
            "X-Forwarded-For": "128.138.131.13",
            "X-Forwarded-Port": "443",
            "X-Forwarded-Proto": "https",
        },
        "multiValueHeaders": {
            "Accept": ["*/*"],
            "Accept-Encoding": ["gzip, deflate, br"],
            "Host": ["qnpkwgob0f.execute-api.us-west-2.amazonaws.com"],
            "User-Agent": ["python-requests/2.27.1"],
            "X-Amzn-Trace-Id": ["Root=1-65aafc00-466eb23e1510d4901018197a"],
            "X-Forwarded-For": ["128.138.131.13"],
            "X-Forwarded-Port": ["443"],
            "X-Forwarded-Proto": ["https"],
        },
        "queryStringParameters": {"instrument": "hit", "data_level": "l0"},
        "multiValueQueryStringParameters": {
            "end_date": ["20271209"],
            "start_date": ["20251101"],
        },
        "pathParameters": None,
        "stageVariables": None,
        "requestContext": {
            "resourceId": "61kave",
            "resourcePath": "/query",
            "httpMethod": "GET",
            "extendedRequestId": "RzxQEHVvvHcEVrw=",
            "requestTime": "19/Jan/2024:22:47:28 +0000",
            "path": "/prod//query",
            "accountId": "021076979309",
            "protocol": "HTTP/1.1",
            "stage": "prod",
            "domainPrefix": "qnpkwgob0f",
            "requestTimeEpoch": 1705704448166,
            "requestId": "e96e6d69-5a0b-4b56-a25b-7d510c1a7fc2",
            "identity": {
                "cognitoIdentityPoolId": None,
                "accountId": None,
                "cognitoIdentityId": None,
                "caller": None,
                "sourceIp": "128.138.131.13",
                "principalOrgId": None,
                "accessKey": None,
                "cognitoAuthenticationType": None,
                "cognitoAuthenticationProvider": None,
                "userArn": None,
                "userAgent": "python-requests/2.27.1",
                "user": None,
            },
            "domainName": "qnpkwgob0f.execute-api.us-west-2.amazonaws.com",
            "apiId": "qnpkwgob0f",
        },
        "body": None,
        "isBase64Encoded": False,
    }

    returned_query = queries.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 200
    assert returned_query["body"] == expected_response


def test_invalid_query(setup_test_data, test_engine):
    "Test that invalid parameters return a 400 status with explanation"
    event = {
        "resource": "/query",
        "path": "/query",
        "httpMethod": "GET",
        "headers": {
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br",
            "Host": "qnpkwgob0f.execute-api.us-west-2.amazonaws.com",
            "User-Agent": "python-requests/2.27.1",
            "X-Amzn-Trace-Id": "Root=1-65aafc00-466eb23e1510d4901018197a",
            "X-Forwarded-For": "128.138.131.13",
            "X-Forwarded-Port": "443",
            "X-Forwarded-Proto": "https",
        },
        "multiValueHeaders": {
            "Accept": ["*/*"],
            "Accept-Encoding": ["gzip, deflate, br"],
            "Host": ["qnpkwgob0f.execute-api.us-west-2.amazonaws.com"],
            "User-Agent": ["python-requests/2.27.1"],
            "X-Amzn-Trace-Id": ["Root=1-65aafc00-466eb23e1510d4901018197a"],
            "X-Forwarded-For": ["128.138.131.13"],
            "X-Forwarded-Port": ["443"],
            "X-Forwarded-Proto": ["https"],
        },
        "queryStringParameters": {"size": "500"},
        "multiValueQueryStringParameters": {
            "end_date": ["20271209"],
            "start_date": ["20251101"],
        },
        "pathParameters": None,
        "stageVariables": None,
        "requestContext": {
            "resourceId": "61kave",
            "resourcePath": "/query",
            "httpMethod": "GET",
            "extendedRequestId": "RzxQEHVvvHcEVrw=",
            "requestTime": "19/Jan/2024:22:47:28 +0000",
            "path": "/prod//query",
            "accountId": "021076979309",
            "protocol": "HTTP/1.1",
            "stage": "prod",
            "domainPrefix": "qnpkwgob0f",
            "requestTimeEpoch": 1705704448166,
            "requestId": "e96e6d69-5a0b-4b56-a25b-7d510c1a7fc2",
            "identity": {
                "cognitoIdentityPoolId": None,
                "accountId": None,
                "cognitoIdentityId": None,
                "caller": None,
                "sourceIp": "128.138.131.13",
                "principalOrgId": None,
                "accessKey": None,
                "cognitoAuthenticationType": None,
                "cognitoAuthenticationProvider": None,
                "userArn": None,
                "userAgent": "python-requests/2.27.1",
                "user": None,
            },
            "domainName": "qnpkwgob0f.execute-api.us-west-2.amazonaws.com",
            "apiId": "qnpkwgob0f",
        },
        "body": None,
        "isBase64Encoded": False,
    }
    expected_response = json.dumps(
        "size is not a valid search parameter. "
        + "Valid search parameters are: "
        + "['file_path', 'instrument', 'data_level', 'descriptor', "
        "'start_date', 'end_date', 'version', 'extension']"
    )
    returned_query = queries.lambda_handler(event=event, context={})

    assert returned_query["statusCode"] == 400
    assert returned_query["body"] == expected_response
