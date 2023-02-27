import unittest

from sds_in_a_box.SDSCode.opensearch_utils.query import Query


class TestQuery(unittest.TestCase):
    """tests for query.py"""

    def setUp(self):
        pass

    def test_get_name_both_dates(self):
        """
        test that the query_dsl method correctly returns the formatted query.
        This tests the flow where there is both a start and end date parameter.
        """
        ## Arrange ##
        query_params = {
            "level": "l0",
            "instrument": "mag",
            "start_date": "2022-01-01T00:00:00",
            "end_date": "2022-01-30T00:00:00",
        }
        query_dsl_true = (
            '{"query": {"bool": {"must": [{"match": {"level": "l0"}}, {"match": '
            '{"instrument": "mag"}}], "filter": {"range": {"date": '
            '{"gte": "2022-01-01T00:00:00", "lte": "2022-01-30T00:00:00"}}}}}}'
        )
        query = Query(query_params)

        ## Act ##
        query_dsl_out = query.query_dsl()

        ## Assert ##
        assert query_dsl_out == query_dsl_true

    def test_get_name_no_start_date(self):
        """
        test that the query_dsl method correctly returns the formatted query.
        This tests the flow where there is an end date parameter, but no start
        date parameter.
        """
        ## Arrange ##
        query_params = {
            "level": "l0",
            "instrument": "mag",
            "end_date": "2022-01-30T00:00:00",
        }
        query_dsl_true = (
            '{"query": {"bool": {"must": [{"match": {"level": "l0"}}, {"match": '
            '{"instrument": "mag"}}], "filter": {"range": {"date": '
            '{"lte": "2022-01-30T00:00:00"}}}}}}'
        )
        query = Query(query_params)

        ## Act ##
        query_dsl_out = query.query_dsl()

        ## Assert ##
        assert query_dsl_out == query_dsl_true

    def test_get_name_no_end_date(self):
        """
        test that the query_dsl method correctly returns the formatted query.
        This tests the flow where there is an a start date parameter, but no end
        date parameter.
        """
        ## Arrange ##
        query_params = {
            "level": "l0",
            "instrument": "mag",
            "start_date": "2022-01-01T00:00:00",
        }
        query_dsl_true = (
            '{"query": {"bool": {"must": [{"match": {"level": "l0"}}, {"match": '
            '{"instrument": "mag"}}], "filter": {"range": {"date": '
            '{"gte": "2022-01-01T00:00:00"}}}}}}'
        )
        query = Query(query_params)

        ## Act ##
        query_dsl_out = query.query_dsl()

        ## Assert ##
        assert query_dsl_out == query_dsl_true

    def test_get_name_no_date(self):
        """
        test that the query_dsl method correctly returns the formatted query.
        This tests the flow where there is neither a start or end date parameter.
        """
        ## Arrange ##
        query_params = {"level": "l0", "instrument": "mag"}
        query_dsl_true = (
            '{"query": {"bool": {"must": [{"match": {"level": "l0"}}, {"match": '
            '{"instrument": "mag"}}]}}}'
        )
        query = Query(query_params)

        ## Act ##
        query_dsl_out = query.query_dsl()

        ## Assert ##
        assert query_dsl_out == query_dsl_true
