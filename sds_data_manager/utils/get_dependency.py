import json
from pathlib import Path


def get_dependency(key):
    """
    Retrieves the value associated with the specified key from a JSON file.

    Parameters
    ----------
    key : str
        The key from the JSON file.

    Returns
    -------
    dict or {}
        The value associated with the provided key in the JSON file.
        If the key does not exist, an empty dictionary is returned.

    Raises
    ------
    FileNotFoundError:
        If the 'dependencies.json' file does not exist in the expected location.
    json.JSONDecodeError:
        If there's an error decoding the JSON file.
    """

    # Construct the path to the JSON file
    dependency_path = Path(__file__).parent.parent / "utils" / "dependencies.json"

    with open(dependency_path) as file:
        data = json.load(file)
        return data.get(key, {})
