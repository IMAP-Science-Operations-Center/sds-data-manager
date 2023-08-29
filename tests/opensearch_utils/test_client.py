import pytest
from openmock import openmock

from sds_data_manager.lambda_code.SDSCode.opensearch_utils.action import Action
from sds_data_manager.lambda_code.SDSCode.opensearch_utils.client import Client
from sds_data_manager.lambda_code.SDSCode.opensearch_utils.document import Document
from sds_data_manager.lambda_code.SDSCode.opensearch_utils.index import Index
from sds_data_manager.lambda_code.SDSCode.opensearch_utils.payload import Payload
from sds_data_manager.lambda_code.SDSCode.opensearch_utils.query import Query


@pytest.fixture()
@openmock
def client():
    # mocked Opensearch client Params
    host = "localhost"
    port = 9000
    hosts = [{"host": host, "port": port}]
    return Client(hosts=hosts)


@pytest.fixture()
def index():
    return Index("test_data")


@pytest.fixture()
def documents(index):
    body1 = {
        "mission": "imap",
        "level": "l0",
        "instrument": "mag",
        "date": "20230112",
        "version": "*",
        "extension": "fits",
    }
    body2 = {
        "mission": "imap",
        "level": "l1",
        "instrument": "mag",
        "date": "20230112",
        "version": "*",
        "extension": "fits",
    }
    body3 = {
        "mission": "imap",
        "level": "l0",
        "instrument": "mag",
        "date": "20221230",
        "version": "*",
        "extension": "fits",
    }
    return [
        Document(index, 1, Action.CREATE, body1),
        Document(index, 2, Action.CREATE, body2),
        Document(index, 3, Action.CREATE, body3),
    ]


def test_create_index(client, index):
    """
    test that the create_index method correctly creates a new index in OpenSearch.
    """
    client.create_index(index)
    assert client.index_exists(index)


def test_send_document_create(client, index):
    """
    Correctly create the specified document in OpenSearch.
    """
    ## Arrange ##
    client.create_index(index)
    action = Action.CREATE
    document = Document(index, 1, action, {"test body": 10})
    document_expected = {
        "_index": "test_data",
        "_type": "_doc",
        "_id": "1",
        "_version": 1,
        "_seq_no": 0,
        "_primary_term": 1,
        "found": True,
        "_source": {"test body": 10},
    }

    ## Act ##
    client.send_document(document)
    document_out = client.get_document(document)

    # openmock does not output the _primary_term and _seq_no. These are two
    # opensearch fields that we're not using. Adding these fields to the output to
    #  highlight that these do exist in a real opensearch document, but are missing
    # from openmock. These missing fields do not impact the test
    document_out["_primary_term"] = 1
    document_out["_seq_no"] = 0

    ## Assert ##
    assert document_expected == document_out


def test_send_document_delete(client, index):
    """
    Correctly delete the specified document in OpenSearch.
    """
    ## Arrange ##
    client.create_index(index)
    action = Action.CREATE
    document = Document(index, 1, action, {"test body": 10})
    client.send_document(document)
    assert client.document_exists(document)

    # Now delete and verify that it was removed
    action = Action.DELETE
    document = Document(index, 1, action, {"test body": 10})
    client.send_document(document)
    assert not client.document_exists(document)


def test_send_document_update(client, index):
    """
    Correctly update the specified document in OpenSearch.
    """
    ## Arrange ##
    client.create_index(index)
    action = Action.CREATE
    document = Document(index, 1, action, {"test body": 10})
    client.send_document(document)
    document.update_body({"test body": 20})
    document.update_action(Action.UPDATE)
    # the version number increments each time a doc is updated (starts at 1)
    # _seq_no increments for each operation performed on the document (starts at 0)
    document_expected = {
        "_index": "test_data",
        "_type": "_doc",
        "_id": "1",
        "_version": 2,
        "_seq_no": 1,
        "_primary_term": 1,
        "found": True,
        "_source": {"test body": 20},
    }

    ## Act ##
    client.send_document(document)
    document_out = client.get_document(document)

    # openmock does not output the _primary_term and _seq_no. These are two
    # opensearch fields that we're not currently using. Adding these fields to the
    # output to highlight that these do exist in a real opensearch document, but
    # are missing from openmock. These missing fields do not impact the test
    document_out["_primary_term"] = 1
    document_out["_seq_no"] = 1

    ## Assert ##
    assert document_out == document_expected


def test_send_document_index(client, index):
    """
    Correctly index the specified document in OpenSearch.
    """
    ## Arrange ##
    client.create_index(index)
    action = Action.INDEX
    document = Document(index, 1, action, {"test body": 10})
    document_expected = {
        "_index": "test_data",
        "_type": "_doc",
        "_id": "1",
        "_version": 1,
        "_seq_no": 0,
        "_primary_term": 1,
        "found": True,
        "_source": {"test body": 10},
    }

    ## Act ##
    client.send_document(document)
    document_out = client.get_document(document)

    # openmock does not output the _primary_term and _seq_no. These are two
    # opensearch fields that we're not using. Adding these fields to the output to
    # highlight that these do exist in a real opensearch document, but are missing
    # from openmock. These missing fields do not impact the test
    document_out["_primary_term"] = 1
    document_out["_seq_no"] = 0

    ## Assert ##
    assert document_out == document_expected


def test_send_payload(client, index):
    """
    Correctly sends a bulk upload of the specified payload to OpenSearch.
    """
    ## Arrange ##
    client.create_index(index)
    action = Action.INDEX
    document1 = Document(index, 1, action, {"test body": 10})
    document2 = Document(index, 2, action, {"test body": 10})
    payload = Payload()
    payload.add_documents([document1, document2])

    document1_expected = {
        "_index": "test_data",
        "_type": "_doc",
        "_id": "1",
        "_version": 1,
        "_seq_no": 0,
        "_primary_term": 1,
        "found": True,
        "_source": {"test body": 10},
    }
    document2_expected = {
        "_index": "test_data",
        "_type": "_doc",
        "_id": "2",
        "_version": 1,
        "_seq_no": 0,
        "_primary_term": 1,
        "found": True,
        "_source": {"test body": 10},
    }

    ## Act ##
    client.send_payload(payload)
    document1_out = client.get_document(document1)
    document2_out = client.get_document(document2)

    # openmock does not output the _primary_term and _seq_no. These are two
    # opensearch fields that we're not using. Adding these fields to the output to
    # highlight that these do exist in a real opensearch document, but are missing
    # from openmock. These missing fields do not impact the test
    document1_out["_primary_term"] = 1
    document1_out["_seq_no"] = 0
    document2_out["_primary_term"] = 1
    document2_out["_seq_no"] = 0

    ## Assert ##
    assert document1_out == document1_expected
    assert document2_out == document2_expected


def test_search(client, index, documents):
    """
    Correctly query the OpenSearch cluster and receive the intended results.
    """
    ## Arrange ##
    search_expected = [
        {
            "_index": "test_data",
            "_type": "_doc",
            "_id": "1",
            "_score": 0.5753642,
            "_source": {
                "mission": "imap",
                "level": "l0",
                "instrument": "mag",
                "date": "20230112",
                "version": "*",
                "extension": "fits",
            },
        }
    ]
    payload = Payload()
    payload.add_documents(documents)
    client.send_payload(payload)
    query = Query(
        {
            "level": "l0",
            "instrument": "mag",
            "start_date": "20230101",
            "end_date": "20230201",
        }
    )

    ## Act ##
    search_out = client.search(query, index)

    # openmock gives a different _score value. This field is not being used and does
    # not affect the test, so it is being removed from the output. The _version
    # field is output by openmock, but not by the real opensearch client, so that
    # is being removed from the output as well and does not affect the test
    search_out[0].pop("_score")
    search_out[0].pop("_version")
    search_expected[0].pop("_score")

    ## Assert ##
    assert search_out == search_expected


def test_scroll_search(client, index):
    """
    Correctly scroll through a large set of results and return without error.
    """
    ## Arrange ##
    search_expected = [
        {
            "_index": "test_data",
            "_type": "_doc",
            "_id": "1",
            "_score": 1.0,
            "_source": {"instrument": 10},
        },
        {
            "_index": "test_data",
            "_type": "_doc",
            "_id": "2",
            "_score": 1.0,
            "_source": {"instrument": 10},
        },
        {
            "_index": "test_data",
            "_type": "_doc",
            "_id": "3",
            "_score": 1.0,
            "_source": {"instrument": 10},
        },
        {
            "_index": "test_data",
            "_type": "_doc",
            "_id": "4",
            "_score": 1.0,
            "_source": {"instrument": 10},
        },
        {
            "_index": "test_data",
            "_type": "_doc",
            "_id": "5",
            "_score": 1.0,
            "_source": {"instrument": 10},
        },
        {
            "_index": "test_data",
            "_type": "_doc",
            "_id": "6",
            "_score": 1.0,
            "_source": {"instrument": 10},
        },
        {
            "_index": "test_data",
            "_type": "_doc",
            "_id": "7",
            "_score": 1.0,
            "_source": {"instrument": 10},
        },
        {
            "_index": "test_data",
            "_type": "_doc",
            "_id": "8",
            "_score": 1.0,
            "_source": {"instrument": 10},
        },
        {
            "_index": "test_data",
            "_type": "_doc",
            "_id": "9",
            "_score": 1.0,
            "_source": {"instrument": 10},
        },
        {
            "_index": "test_data",
            "_type": "_doc",
            "_id": "10",
            "_score": 1.0,
            "_source": {"instrument": 10},
        },
        {
            "_index": "test_data",
            "_type": "_doc",
            "_id": "11",
            "_score": 1.0,
            "_source": {"instrument": 10},
        },
        {
            "_index": "test_data",
            "_type": "_doc",
            "_id": "12",
            "_score": 1.0,
            "_source": {"instrument": 10},
        },
        {
            "_index": "test_data",
            "_type": "_doc",
            "_id": "13",
            "_score": 1.0,
            "_source": {"instrument": 10},
        },
        {
            "_index": "test_data",
            "_type": "_doc",
            "_id": "14",
            "_score": 1.0,
            "_source": {"instrument": 10},
        },
        {
            "_index": "test_data",
            "_type": "_doc",
            "_id": "15",
            "_score": 1.0,
            "_source": {"instrument": 10},
        },
        {
            "_index": "test_data",
            "_type": "_doc",
            "_id": "16",
            "_score": 1.0,
            "_source": {"instrument": 10},
        },
        {
            "_index": "test_data",
            "_type": "_doc",
            "_id": "17",
            "_score": 1.0,
            "_source": {"instrument": 10},
        },
        {
            "_index": "test_data",
            "_type": "_doc",
            "_id": "18",
            "_score": 1.0,
            "_source": {"instrument": 10},
        },
        {
            "_index": "test_data",
            "_type": "_doc",
            "_id": "19",
            "_score": 1.0,
            "_source": {"instrument": 10},
        },
    ]

    payload = Payload()
    for i in range(1, 20):
        document = Document(index, i, Action.CREATE, {"instrument": 10})
        payload.add_documents(document)

    client.send_payload(payload)

    query = Query({"instrument": 10})

    ## Act ##
    search_out = client.search(query, index)

    # openmock gives a different _score value than opensearch. This field is not
    # being used and does not affect the test, so it is being removed from the
    # output. The _version field is output by openmock, but not by the real
    # opensearch client, so that is being removed from the output as well and does
    # not affect the test
    for search in search_out:
        search.pop("_score")
        search.pop("_version")
    for search in search_expected:
        search.pop("_score")

    ## Assert ##
    assert search_out == search_expected
