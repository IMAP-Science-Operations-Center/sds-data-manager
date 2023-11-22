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
    dict
        The value associated with the provided key in the JSON file.
    """

    # Construct the path to the JSON file
    dependency_path = (
        Path(__file__).parent.parent / "lambda_code" / "downstream_dependents.json"
    )

    with open(dependency_path) as file:
        data = json.load(file)
        value = data.get(key)

        if value is None:
            raise KeyError(f"Key '{key}' not found in the JSON file.")

        return value
