import json

VALID_PARAMS = ["instrument", "level", "start_date", "end_date"]


class Query:
    """
    Query class to represent an AWS OpenSearch query domain-specific language (DSL),
    providing a range of search options. Query objects will be used by the Client
    search method and used in OpenSearch searches.

    ...

    Attributes
    ----------
    query_params: dict
        dictionary containing the query field (dict key) and the query text
        (dict value).
    query_dsl_formatted: dict
        dictionary containing the json query in the OpenSearch DSL format.
    query_size: int, optional
        number of results to return. default is 10.


    Methods
    -------
    query_dsl: str
        returns the query in the OpenSearch Query DSL format
    size: int
        returns the number of results the query is allowed to return in the search.
    """

    def __init__(self, query_params, size=10):
        self.query_params = query_params
        self.query_dsl_formatted = self._build_query_dsl(query_params)
        self.query_size = size

    def query_dsl(self):
        """Returns the query parameters in the AWS OpenSearch Query DSL format"""
        return self.query_dsl_formatted

    def size(self):
        """Returns the number of results the query is allowed to return in the search"""
        return self.query_size

    def _build_query_dsl(self, query_params):
        """
        Builds a Query DSL using a dictionary with field:value pairings.

        Parameters
        ----------
        query_params: dict
            dictionary containing field:value search parameters.

        """
        # define the query structure
        query = {"query": {"bool": {}}}
        query_match_structure = {"match": {}}
        query_date_structure = {"range": {"date": {}}}

        # remove all params that are not valid
        query_params = {
            param: query_params[param]
            for param in query_params
            if param in VALID_PARAMS
        }

        # create the query
        for param in query_params:
            # create a date query using start and end date parameters
            if param in ("start_date", "end_date"):
                # create the filter strcture for date queries if it hasn't
                # already been created
                if "filter" not in query["query"]["bool"]:
                    query["query"]["bool"]["filter"] = query_date_structure
                # create the greater than or equal to (gte) start date query
                if param == "start_date":
                    query["query"]["bool"]["filter"]["range"]["date"][
                        "gte"
                    ] = query_params[param]
                # create the less than or equal to (lte) end date query
                if param == "end_date":
                    query["query"]["bool"]["filter"]["range"]["date"][
                        "lte"
                    ] = query_params[param]
            else:
                # create the must query structure if it doesn't
                # already exist
                if "must" not in query["query"]["bool"]:
                    query["query"]["bool"]["must"] = []

                # add the search parameters to the must query structure
                query_match = query_match_structure.copy()
                query_match["match"] = {param: query_params[param]}
                query["query"]["bool"]["must"].append(query_match)

        return query

    def __repr__(self):
        return json.dumps(self.query_dsl_formatted)
