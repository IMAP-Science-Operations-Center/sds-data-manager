import unittest

from sds_data_manager.lambda_code.SDSCode.opensearch_utils.action import Action
from sds_data_manager.lambda_code.SDSCode.opensearch_utils.document import Document
from sds_data_manager.lambda_code.SDSCode.opensearch_utils.index import Index
from sds_data_manager.lambda_code.SDSCode.opensearch_utils.payload import Payload


class TestPayload(unittest.TestCase):
    """tests for payload.py"""

    def setUp(self):
        self.index = Index("test_data")

    def test_add_documents(self):
        """
        test that the add_documents method correctly adds documents to the payload.
        """
        ## Arrange ##
        payload = Payload()
        body = (
            '{"mission":"imap", "level":"l0", "instrument":"*", "date":"*", '
            '"version":"*", "extension":"fits"}'
        )
        document = Document(self.index, 1, Action.CREATE, body)

        ## Act ##
        payload.add_documents(document)

        ## Assert ##
        assert payload.get_contents() == document.get_contents()

    def test_add_documents_list(self):
        """
        Correctly add a list of documents to the payload.
        """
        ## Arrange ##
        payload = Payload()
        body1 = (
            '{"mission":"imap", "level":"l0", "instrument":"*", "date":"*", '
            '"version":"*", "extension":"fits"}'
        )
        body2 = (
            '{"mission":"imap", "level":"l2", "instrument":"*", "date":"*", '
            '"version":"*", "extension":"fits"}'
        )
        document1 = Document(self.index, 1, Action.CREATE, body1)
        document2 = Document(self.index, 1, Action.CREATE, body2)
        payload_contents_true = document1.get_contents() + document2.get_contents()

        ## Act ##
        payload.add_documents([document1, document2])

        ## Assert ##
        assert payload.get_contents() == payload_contents_true

    def test_add_documents_error(self):
        """
        Correctly throw an error when passed neither a list nor document.
        """
        ## Arrange ##
        payload = Payload()
        bad_document = "string, not a document"

        ## Act / Assert ##
        self.assertRaises(TypeError, payload.add_documents, bad_document)

    def test_add_documents_bad_list(self):
        """
        Correctly throw an error when passed a list of containing objects that
        are not of type Document.
        """
        ## Arrange ##
        payload = Payload()
        bad_document_list = ["string, not a document", 1]

        ## Act / Assert ##
        self.assertRaises(TypeError, payload.add_documents, bad_document_list)

    def test_get_contents(self):
        """
        Correctly returns the contents of the payload as a string.
        """
        payload = Payload()
        document = Document(self.index, 1, Action.CREATE, '{"testbody":"test1"}')
        payload.add_documents(document)
        contents_true = document.get_contents()

        ## Act ##
        contents_out = payload.get_contents()

        ## Assert ##
        assert contents_true == contents_out
