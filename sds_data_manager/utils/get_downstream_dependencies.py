import json
from pathlib import Path


def get_downstream_dependencies(key):
    """
    Retrieves downstream dependencies of a given instrument.

    Parameters
    ----------
    key : str
        The key from the JSON file.

    Returns
    -------
    dict or {}
        The value associated with the provided key in the JSON file.
        If the key does not exist, an empty dictionary is returned.
    """

    # Construct the path to the JSON file
    dependency_path = (
        Path(__file__).parent.parent / "utils" / "downstream_dependents.json"
    )

    with open(dependency_path) as file:
        data = json.load(file)
        return data.get(key, {})
