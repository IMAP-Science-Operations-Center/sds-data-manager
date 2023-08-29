import json

from .action import Action
from .index import Index


class Document:
    """
    Class to represent an OpenSearch document.

    ...

    Attributes
    ----------
    index: Index
        the index that the document should be associated with.
    identifier: int, str
        the identifier to be associated with the document in OpenSearch.
    body: str
        the body of the document.
    action: Action
        the action for OpenSearch to perform on the document.
    contents: str
        the complete document formatted as a single API request.
    size: int
        the size of the document in bytes.

    Methods
    -------
    update_body(body):
        updates the body of the document with a string.
    update_action(action):
        updates the action associated with the document.
    get_body():
        returns the body of the document.
    get_index():
        returns the name of the index associated with the document.
    get_action():
        returns the action associated with the document.
    get_identifier():
        returns the identifier associated with the document.
    get_contents():
        returns full contents of the document as a str. this includes
        the index, action, identifier, and body.
    size_in_bytes():
        returns the size of the encoded (ascii) document contents in bytes.
    """

    def __init__(
        self,
        index,
        doc_id,
        action,
        body=None,
    ):
        self.index = Index.validate_index(index)
        self.identifier = self._validate_identifier(doc_id)
        self.action = Action.validate_action(action)
        self.body = body or {}
        self.contents = ""
        self.size = 0

        self._update_contents()

    def update_body(self, body):
        """
        Updates the body of the document.

        Parameters
        ----------
        body: str
            updated body text for the document.
        """
        if type(body) is dict:
            self.body = body
            self._update_contents()
        else:
            raise TypeError(
                "Document body passed in as type {}, but must be of type dict".format(
                    type(body)
                )
            )

    def update_action(self, action):
        """
        Updates the action of the document.

        Parameters
        ----------
        action: Action
            action to be performed on the document by OpenSearch.
        """
        self.action = Action.validate_action(action)

    def get_body(self):
        """Returns the body of the document as a string."""
        return self.body

    def get_index(self):
        """Returns the name of the document's index as a string."""
        return self.index.get_name()

    def get_action(self):
        """Returns the document's action as a string."""
        return self.action

    def get_identifier(self):
        """Returns the document's id as an int."""
        return self.identifier

    def get_contents(self):
        """Returns the full contents of the document as a string."""
        return self.contents

    def size_in_bytes(self):
        """Returns the size of the document's bulk request json string in bytes."""
        return self.size

    def _update_contents(self):
        action_string = (
            '{ "'
            + self.action.value
            + '": { "_index": "'
            + self.index.get_name()
            + '", "_id": "'
            + self.identifier
            + '" } }\n'
        )
        self.contents = action_string + json.dumps(self.body) + "\n"
        self.size = len(self.contents.encode("ascii"))

    def _validate_identifier(self, identifier):
        if type(identifier) is str or type(identifier) is int:
            return str(identifier)
        else:
            raise TypeError(
                f"Identifier is type {type(identifier)}, but must be type str or int"
            )

    @staticmethod
    def is_document(document):
        """
        static method that returns whether the input is type Document.

        Parameters
        ----------
        document:
            input to check if it is type Document.
        """
        return type(document) is Document

    def __repr__(self):
        return str(self.contents)
