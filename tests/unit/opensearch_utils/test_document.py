import unittest

from sds_data_manager.lambda_code.SDSCode.opensearch_utils.action import Action
from sds_data_manager.lambda_code.SDSCode.opensearch_utils.document import Document
from sds_data_manager.lambda_code.SDSCode.opensearch_utils.index import Index


class TestDocument(unittest.TestCase):
    """tests for document.py"""

    def setUp(self):
        self.document_body = {
            "mission": "imap",
            "level": "l0",
            "instrument": "*",
            "date": "*",
            "version": "*",
            "extension": "fits",
        }
        self.index_name = "test_data"
        self.index = Index(self.index_name)
        self.identifier = 1
        self.action = Action.CREATE

    def test_update_body(self):
        """
        test that the update_body method correctly updates the body
        of the document.
        """
        ## Arrange ##
        document = Document(self.index, self.identifier, self.action)
        body_true = self.document_body

        ## Act ##
        document.update_body(self.document_body)
        body_out = document.get_body()

        ## Assert ##
        assert body_out == body_true

    def test_update_body_error(self):
        """
        test that the update_body method throws an error when the
        wrong type is passed in.
        """
        ## Arrange ##
        body = 12
        document = Document(self.index, self.identifier, self.action)

        ## Act / Assert ##
        self.assertRaises(TypeError, document.update_body, body)

    def test_update_action(self):
        """
        test that the update_action method updates the action
        associated with the document.
        """
        ## Arrange ##
        document = Document(self.index, self.identifier, self.action)
        action_true = Action.INDEX

        ## Act ##
        document.update_action(action_true)
        action_out = document.get_action()

        ## Assert ##
        assert action_out == action_true

    def test_get_body(self):
        """
        test that the get_body method returns the document body as a string.
        """
        ## Arrange ##
        document = Document(
            self.index, self.identifier, self.action, self.document_body
        )
        body_true = self.document_body

        ## Act ##
        body_out = document.get_body()

        ## Assert ##
        assert body_out == body_true

    def test_get_index(self):
        """
        Correctly return the document's index's name of the index.
        """
        ## Arrange ##
        document = Document(self.index, self.identifier, self.action)
        index_true = self.index.get_name()

        ## Act ##
        index_out = document.get_index()

        ## Assert ##
        assert index_out == index_true

    def test_get_action(self):
        """
        Correctly return the document's action.
        """
        ## Arrange ##
        document = Document(self.index, self.identifier, self.action)
        action_true = self.action

        ## Act ##
        action_out = document.get_action()

        ## Assert ##
        assert action_out == action_true

    def test_get_identifier(self):
        """
        Correctly return the document's identifier.
        """
        ## Arrange ##
        identifier_true = str(self.identifier)
        document = Document(self.index, identifier_true, self.action)

        ## Act ##
        identifier_out = document.get_identifier()

        ## Assert ##
        assert identifier_out == identifier_true

    def test_get_contents(self):
        """
        Correctly return the document contents as a string.
        """
        ## Arrange ##
        document = Document(
            self.index, self.identifier, self.action, self.document_body
        )
        contents_true = (
            '{ "create": { "_index": "test_data", "_id": "1" } }\n'
            '{"mission": "imap", "level": "l0", "instrument": "*", '
            '"date": "*", "version": "*", "extension": "fits"}\n'
        )

        ## Act ##
        contents_out = document.get_contents()

        ## Assert ##
        assert contents_out == contents_true

    def test_size_in_bytes(self):
        """
        Correctly return the document's size in bytes.
        """
        ## Arrange ##
        document = Document(
            self.index, self.identifier, self.action, self.document_body
        )
        doc = (
            '{ "create": { "_index": "test_data", "_id": "1" } }\n'
            "{'mission': 'imap', 'level': 'l0', 'instrument': '*', "
            "'date': '*', 'version': '*', 'extension': 'fits'}\n"
        )
        size_in_bytes_true = len(doc.encode("ascii"))

        ## Act ##
        size_in_bytes_out = document.size_in_bytes()

        ## Assert ##
        assert size_in_bytes_out == size_in_bytes_true

    def test_is_document_true(self):
        """
        Correctly return whether the input is of type Document.
        """
        ## Arrange ##
        document = Document(self.index, self.identifier, self.action)
        result_true = True

        ## Act ##
        result_out = Document.is_document(document)

        ## Assert ##
        assert result_out == result_true

    def test_is_document_false(self):
        """
        Correctly return whether the input is of type Document.
        """
        ## Arrange ##
        document = "string, not a document"
        result_true = False

        ## Act ##
        result_out = Document.is_document(document)

        ## Assert ##
        assert result_out == result_true
