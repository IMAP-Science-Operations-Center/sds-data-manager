from .document import Document


class Payload:
    """
    Class to represent an OpenSearch bulk document payload.

    ...


    Attributes
    ----------
    payload_contents: list
        list of json strings representing the full payload contents,
        broken up into a list to avoid request limits when sending to OpenSearch.

    Methods
    -------
    add_documents(documents):
        adds document(s) to the payload to prepare for a
        bulk upload.
    get_contents():
        returns the full payload contents as a string.
    """

    def __init__(self):
        self.payload_contents = []

    def add_documents(self, documents):
        """
        Add document(s) to the payload for a bulk upload.

        Parameters
        ----------
        documents: Document, list of Documents
            document(s) to be added to the payload in preparation for a bulk upload.
        """
        if Document.is_document(documents):
            self._add_to_payload(documents)

        elif isinstance(documents, list):
            # check that all the objects in documents are of type Document
            if all(Document.is_document(doc) for doc in documents):
                for doc in documents:
                    self._add_to_payload(doc)

            else:
                raise TypeError(
                    "Document list contained at least one object that was not "
                    "of type Document"
                )

        else:
            raise TypeError(
                f"Input was type {type(documents)} but must be type Document "
                "or list of Documents."
            )

    def get_contents(self):
        """Returns the contents of the payload as a string."""
        full_contents = "".join(self.payload_contents)
        return full_contents

    def payload_chunks(self):
        """Returns a list of payload documents chunked to avoid bulk upload limits"""
        return self.payload_contents

    def __repr__(self):
        return str(self.payload_contents)

    def _add_to_payload(self, document):
        # TODO: not sure what the actual request limit is or how it's
        # determined, but the size of the encoded string seems to be
        # the most consistent way to check if the limit is hit and that
        # limit seems to be somewhere around the number of bytes below.
        # Need to figure out how the request limits work.
        request_limit = 5281500  # bytes

        # check if the payload is empty and if the payload with the
        # new document added would still be under the request limit
        if (
            len(self.payload_contents) > 0
            and self._size_in_bytes(self.payload_contents[-1])
            + document.size_in_bytes()
            < request_limit
        ):
            # concat the new document
            self.payload_contents[-1] = (
                self.payload_contents[-1] + document.get_contents()
            )
        else:
            # start a new payload chunk with the new document
            self.payload_contents.append(document.get_contents())

    def _size_in_bytes(self, payload):
        """Returns the size of the payload contents in bytes."""
        return len(payload.encode("ascii"))
