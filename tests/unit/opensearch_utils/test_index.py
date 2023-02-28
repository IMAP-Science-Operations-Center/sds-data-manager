import unittest

from sds_data_manager.lambda_code.SDSCode.opensearch_utils.index import Index


class TestIndex(unittest.TestCase):
    """tests for index.py"""

    def setUp(self):
        self.index_name = "python-test-index3"
        self.index_body = {"settings": {"index": {"number_of_shards": 4}}}

    def test_get_name(self):
        """
        test that the get_name method correctly returns the index name as a string.
        """
        ## Arrange ##
        index_name_true = self.index_name
        index = Index(index_name_true, self.index_body)

        ## Act ##
        index_name_out = index.get_name()

        ## Assert ##
        assert index_name_true == index_name_out

    def test_get_body(self):
        """
        test that the get_body method correctly returns the index body as a dict.
        """
        ## Arrange ##
        index_body_true = self.index_body
        index = Index(self.index_name, index_body_true)

        ## Act ##
        index_body_out = index.get_body()

        ## Assert ##
        assert index_body_true == index_body_out

    def test_validate_index(self):
        """
        Correctly determine the input to be of type Index.
        """
        ## Arrange ##
        index_true = Index(self.index_name, self.index_body)

        ## Act ##
        index_out = Index.validate_index(index_true)

        ## Assert ##
        assert index_true == index_out

    def test_validate_index_error(self):
        """
        Correctly determine the input to not be of type Index.
        """
        ## Arrange ##
        index = "string, not an Index"

        ## Act / Assert ##
        self.assertRaises(TypeError, Index.validate_index, index)

    def test_repr(self):
        """
        test that the object is correctly represented as a string.
        """
        ## Arrange ##
        my_index = Index(self.index_name, self.index_body)
        index_string_true = str(
            {"python-test-index3": {"settings": {"index": {"number_of_shards": 4}}}}
        )

        ## Act ##
        index_string_out = str(my_index)

        ## Assert ##
        assert index_string_true == index_string_out
