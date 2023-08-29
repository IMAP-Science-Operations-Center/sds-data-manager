import pytest

from sds_data_manager.lambda_code.SDSCode.opensearch_utils.action import Action
from sds_data_manager.lambda_code.SDSCode.opensearch_utils.document import Document
from sds_data_manager.lambda_code.SDSCode.opensearch_utils.index import Index


@pytest.fixture()
def document_body():
    return {
        "mission": "imap",
        "level": "l0",
        "instrument": "*",
        "date": "*",
        "version": "*",
        "extension": "fits",
    }


@pytest.fixture()
def document(document_body):
    index = Index("test_data")
    identifier = 1
    action = Action.CREATE
    return Document(index, identifier, action, document_body)


def test_update_body(document):
    """
    test that the update_body method correctly updates the body
    of the document.
    """
    new_body = {"test": "test"}
    document.update_body(new_body)
    assert document.get_body() == new_body


def test_update_body_error(document):
    """
    test that the update_body method throws an error when the
    wrong type is passed in.
    """
    ## Arrange ##
    body = 12

    with pytest.raises(TypeError):
        document.update_body(body)


def test_update_action(document):
    """
    test that the update_action method updates the action
    associated with the document.
    """
    ## Arrange ##
    action_expected = Action.INDEX

    ## Act ##
    document.update_action(action_expected)
    action_out = document.get_action()

    ## Assert ##
    assert action_out == action_expected


def test_get_body(document, document_body):
    """
    test that the get_body method returns the document body as a string.
    """
    assert document.get_body() == document_body


def test_get_index(document):
    """
    Correctly return the document's index's name of the index.
    """
    assert document.get_index() == "test_data"


def test_get_action(document):
    """
    Correctly return the document's action.
    """
    assert document.get_action() == Action.CREATE


def test_get_identifier(document):
    """
    Correctly return the document's identifier.
    """
    assert document.get_identifier() == "1"


def test_get_contents(document):
    """
    Correctly return the document contents as a string.
    """
    contents_expected = (
        '{ "create": { "_index": "test_data", "_id": "1" } }\n'
        '{"mission": "imap", "level": "l0", "instrument": "*", '
        '"date": "*", "version": "*", "extension": "fits"}\n'
    )

    contents_out = document.get_contents()
    assert contents_out == contents_expected


def test_size_in_bytes(document):
    """
    Correctly return the document's size in bytes.
    """
    doc = (
        '{ "create": { "_index": "test_data", "_id": "1" } }\n'
        "{'mission': 'imap', 'level': 'l0', 'instrument': '*', "
        "'date': '*', 'version': '*', 'extension': 'fits'}\n"
    )
    size_in_bytes_expected = len(doc.encode("ascii"))
    assert document.size_in_bytes() == size_in_bytes_expected


def test_is_document_expected(document):
    """
    Correctly return whether the input is of type Document.
    """
    assert Document.is_document(document)


def test_is_document_false():
    """
    Correctly return whether the input is of type Document.
    """
    ## Arrange ##
    document = "string, not a document"
    assert not Document.is_document(document)
