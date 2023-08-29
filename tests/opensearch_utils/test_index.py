import pytest

from sds_data_manager.lambda_code.SDSCode.opensearch_utils.index import Index


@pytest.fixture()
def index_name():
    return "python-test-index3"


@pytest.fixture()
def index_body():
    return {"settings": {"index": {"number_of_shards": 4}}}


@pytest.fixture()
def index(index_name, index_body):
    return Index(index_name, index_body)


def test_get_name(index, index_name):
    """
    test that the get_name method correctly returns the index name as a string.
    """
    assert index_name == index.get_name()


def test_get_body(index, index_body):
    """
    test that the get_body method correctly returns the index body as a dict.
    """
    assert index.get_body() == index_body


def test_validate_index(index):
    """
    Correctly determine the input to be of type Index.
    """
    assert index == Index.validate_index(index)


def test_validate_index_error():
    """
    Correctly determine the input to not be of type Index.
    """
    ## Arrange ##
    index = "string, not an Index"
    with pytest.raises(TypeError):
        Index.validate_index(index)


def test_repr(index):
    """
    test that the object is correctly represented as a string.
    """
    ## Arrange ##
    index_string_expected = str(
        {"python-test-index3": {"settings": {"index": {"number_of_shards": 4}}}}
    )

    assert str(index) == index_string_expected
