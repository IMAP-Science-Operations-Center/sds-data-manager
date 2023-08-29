from enum import Enum


class Action(Enum):
    """
    Enum class to represent an OpenSearch API action that can be performed on
    documents. Possible actions are create, delete, update, and index.

    ...

    Attributes
    ----------
    Create : "create"
        Creates a new document or returns 409 response if it already exists.
    Delete : "delete"
        Deletes the document.
    Update : "update"
        Updates the document.
    Index  : "index"
        Creates a new document if it doesn't exist, updates the existing
        document if it does.

    Methods
    -------
    validate_action(action):
        Static method to validate that the input is of
        type Action.

    """

    CREATE = "create"
    DELETE = "delete"
    UPDATE = "update"
    INDEX = "index"

    @staticmethod
    def is_action(action):
        """
        Static method that returns whether the input is type Action.

        Parameters
        ----------
        document:
            input to check if it is type Action.
        """
        return type(action) is Action

    @staticmethod
    def validate_action(action):
        """
        Static method used to validate whether an object
        is of type Action. If the input is an Action, the same
        Action is returned. If the input is not an Action, an
        error is raised.

        Parameters
        ----------
        action : an object to be validated as an action.

        Returns
        -------
        Action
            the validated action that was input.

        """
        if Action.is_action(action):
            return action
        else:
            raise TypeError(f"Input is type {type(action)}, but must be type Action")
