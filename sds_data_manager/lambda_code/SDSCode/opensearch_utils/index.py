class Index:
    """
    Class to represent an OpenSearch index.

    ...

    Attributes
    ----------
    name: str
        name to be given to the index.
    body: dict, optional
        dictionary containing index options.

    Methods
    -------
    get_name():
        returns the name of the index as a string.
    get_body():
        returns the body of the index as a dict.
    validate_index(index):
        Static method to validate that the input is of
        type Index.
    """

    def __init__(self, name, body=None):
        self.name = name
        self.body = body

    def get_name(self):
        """Returns the name of the index as a string."""
        return self.name

    def get_body(self):
        """Returns the body of the index as a dictionary."""
        return self.body

    def __repr__(self):
        return str({self.name: self.body})

    @staticmethod
    def validate_index(index):
        """
        Static method used to validate whether an object
        is of type Index. If the input is an Index, the same
        Index is returned. If the input is not an Index, an
        error is raised.

        Parameters
        ----------
        index : an object to be validated as an Index.

        Returns
        -------
        Index
            the validated index, same the object input.

        """
        if type(index) is Index:
            return index
        else:
            raise TypeError(f"Input is type {type(index)}, but must be type Index")
