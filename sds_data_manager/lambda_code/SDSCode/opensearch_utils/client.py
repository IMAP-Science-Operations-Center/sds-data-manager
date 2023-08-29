import opensearchpy

from .action import Action


class Client:
    """
    Class to represent the connection with the OpenSearch cluster.

    ...

    Attributes
    ----------
    hosts: list
        list of dicts containing the host and port.
        ex: [{'host': host, 'port': port}]
    http_auth: tuple
        tuple containing the authentication username and password for the
        OpenSearch cluster.
    use_ssl: boolean
        turn on / off SSL.
    verify_certs: boolean
        turn on / off verification of SSL certificates.
    connection_class:



    Methods
    -------
    create_index(index):
        creates an index in the OpenSearch cluster.
    delete_index(index):
        deletes an index in the OpenSearch cluster.
    index_exists(index):
        checks whether a particular index exists in the OpenSearch cluster.
    document_exists(document):
        checks whether a particular document exists in the OpenSearch cluster.
    send_document(document):
        sends a document to the OpenSearch cluster with its associated action.
    send_payload(payload):
        Sends a bulk payload of documents to the OpenSearch cluster.


    """

    def __init__(
        self,
        hosts,
        http_auth=None,
        use_ssl=True,
        verify_certs=True,
        connnection_class=opensearchpy.RequestsHttpConnection,
    ):
        self.hosts = hosts
        self.http_auth = http_auth
        self.use_ssl = use_ssl
        self.verify_certs = verify_certs
        self.connnection_class = connnection_class
        self.client = opensearchpy.OpenSearch(
            hosts=self.hosts,
            http_auth=self.http_auth,
            use_ssl=self.use_ssl,
            verify_certs=self.verify_certs,
            connection_class=self.connnection_class,
        )

    def create_index(self, index):
        """
        Creates an index in the OpenSearch cluster.

        Parameters
        ----------
        index: Index
            index to be created in the OpenSearch cluster.

        """
        self.client.indices.create(index=index.get_name(), body=index.get_body())

    def delete_index(self, index):
        """
        Deletes an index in the OpenSearch cluster.

        Parameters
        ----------
        index: Index
            index to be deleted in the OpenSearch cluster.

        """
        self.client.indices.delete(index=index.get_name())

    def index_exists(self, index):
        """
        Returns an boolean indicating whether particular index exists.

        Parameters
        ----------
        index: Index, list
            index or list of indicies.
        """
        return self.client.indices.exists(index.get_name())

    def document_exists(self, document):
        """
        Returns an boolean indicating whether the document exists in the index.

        Parameters
        ----------
        document: Document
            document to check if it exists in the OpenSearch cluster.
        """
        return self.client.exists(
            index=document.get_index(), id=document.get_identifier()
        )

    def send_document(self, document, action_override=None):
        """
        Sends the document to OpenSearch using the action associated with
        the document.

        Parameters
        ----------
        document: Document
            document to be sent to the OpenSearch cluster.
        """

        # override the action if specified
        action = self._override_action(document, action_override)

        if action == Action.CREATE:
            self._create_document(document)
        elif action == Action.DELETE:
            self._delete_document(document)
        elif action == Action.UPDATE:
            self._update_document(document)
        elif action == Action.INDEX:
            self._index_document(document)

    def send_payload(self, payload):
        """
        Sends a bulk payload of documents to the OpenSearch cluster.

        Parameters
        ----------
        payload: Payload
            payload containing bulk documents to be sent to the OpenSearch cluster.
        """
        for chunk in payload.payload_chunks():
            self.client.bulk(chunk, params={"request_timeout": 1000000})

    def get_document(self, document):
        """Returns the specified document"""
        return self.client.get(index=document.get_index(), id=document.get_identifier())

    def search(self, query, index):
        """
        Searches the OpenSearch cluster using the provided query object.

        Parameters
        ----------
        query: Query
            query object instantiated with the desired query parameters.
        index: Index
            OpenSearch index to use for the search.
        """
        # search the opensearch instance with scroll to handle larger responses
        result = self.client.search(
            body=query.query_dsl(), index=index.get_name(), params={"scroll": "1m"}
        )
        scroll_id = result["_scroll_id"]
        scroll_size = len(result["hits"]["hits"])
        counter = 0
        full_result = result["hits"]["hits"]

        # scroll through the results and add results to list
        while scroll_size > 0:
            counter += scroll_size
            result = self.client.scroll(scroll_id=scroll_id, scroll="1m")
            full_result += result["hits"]["hits"]
            scroll_id = result["_scroll_id"]
            scroll_size = len(result["hits"]["hits"])

        return full_result

    def close(self):
        """Close the Transport and all internal connections"""
        self.client.close()

    def _override_action(self, document, action):
        if action is None or not Action.is_action(action):
            action = document.get_action()
        return action

    def _create_document(self, document):
        """
        Creates the document in the OpenSearch cluster. Returns a 409 response
        when a document with a same identifier already exists in the index.

        Parameters
        ----------
        document: Document
            Document to be added to the OpenSearch cluster.

        """
        self.client.create(
            index=document.get_index(),
            id=document.get_identifier(),
            body=document.get_body(),
        )

    def _delete_document(self, document):
        """
        Deletes the document in the OpenSearch cluster.

        Parameters
        ----------
        document: Document
            Document to be deleted from the OpenSearch cluster.

        """
        self.client.delete(index=document.get_index(), id=document.get_identifier())

    def _update_document(self, document):
        """
        Updates the document in the OpenSearch cluster if it exists, returns an error
        if it doesn't exist.

        Parameters
        ----------
        document: Document
             Document to be updated in the OpenSearch cluster.

        """
        body = {"doc": document.get_body()}
        self.client.update(
            index=document.get_index(), id=document.get_identifier(), body=body
        )

    def _index_document(self, document):
        """
        Creates the document in the OpenSearch cluster if it does not already exist.
        If the document does exist, it will update the document.

        Parameters
        ----------
         document: Document
            Document to be created or updated in the OpenSearch cluster.

        """
        self.client.index(
            index=document.get_index(),
            id=document.get_identifier(),
            body=document.get_body(),
        )
