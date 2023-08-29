import pytest

from sds_data_manager.lambda_code.SDSCode.opensearch_utils.action import Action
from sds_data_manager.lambda_code.SDSCode.opensearch_utils.document import Document
from sds_data_manager.lambda_code.SDSCode.opensearch_utils.index import Index
from sds_data_manager.lambda_code.SDSCode.opensearch_utils.payload import Payload


@pytest.fixture()
def index():
    return Index("test_data")


@pytest.fixture()
def payload():
    return Payload()


def test_add_documents(payload, index):
    """
    test that the add_documents method correctly adds documents to the payload.
    """
    body = (
        '{"mission":"imap", "level":"l0", "instrument":"*", "date":"*", '
        '"version":"*", "extension":"fits"}'
    )
    document = Document(index, 1, Action.CREATE, body)

    ## Act ##
    payload.add_documents(document)

    ## Assert ##
    assert payload.get_contents() == document.get_contents()


def test_add_documents_list(payload, index):
    """
    Correctly add a list of documents to the payload.
    """
    body1 = (
        '{"mission":"imap", "level":"l0", "instrument":"*", "date":"*", '
        '"version":"*", "extension":"fits"}'
    )
    body2 = (
        '{"mission":"imap", "level":"l2", "instrument":"*", "date":"*", '
        '"version":"*", "extension":"fits"}'
    )
    document1 = Document(index, 1, Action.CREATE, body1)
    document2 = Document(index, 1, Action.CREATE, body2)
    payload_contents_expected = document1.get_contents() + document2.get_contents()

    ## Act ##
    payload.add_documents([document1, document2])

    ## Assert ##
    assert payload.get_contents() == payload_contents_expected


def test_add_documents_error(payload):
    """
    Correctly throw an error when passed neither a list nor document.
    """
    bad_document = "string, not a document"
    with pytest.raises(TypeError):
        payload.add_documents(bad_document)


def test_add_documents_bad_list(payload):
    """
    Correctly throw an error when passed a list of containing objects that
    are not of type Document.
    """
    bad_document_list = ["string, not a document", 1]
    with pytest.raises(TypeError):
        payload.add_documents(bad_document_list)


def test_get_contents(payload, index):
    """
    Correctly returns the contents of the payload as a string.
    """
    document = Document(index, 1, Action.CREATE, '{"testbody":"test1"}')
    payload.add_documents(document)
    contents_expected = document.get_contents()

    contents_out = payload.get_contents()
    assert contents_expected == contents_out
