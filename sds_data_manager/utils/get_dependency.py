import json
import sys
from pathlib import Path

def get_dependency(key):
    """
    Retrieves dependencies for each instrument/level.

    Parameters
    ----------
    key : str
        Target name

    Returns
    -------
    packets : list
        List of all the dependencies
    """
    dependency_path=Path(sys.modules[__name__.split(
        '.')[0]].__file__).parent / 'utils' / 'dependencies.json'

    with open(dependency_path, 'r') as f:
        dependencies = json.load(f)
        return dependencies.get(key, [])
