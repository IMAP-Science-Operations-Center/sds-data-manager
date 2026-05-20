"""Simple utilities for reading dependency configurations."""

import logging
import os
from pathlib import Path

import requests
import yaml
from imap_data_access import VALID_INSTRUMENTS

from ...api_lambdas import upload_api
from .types import DependencyNode

# Logger setup
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class DependencyConfigReader:
    """Dependency configuration reader.

    This class encapsulates all operations for reading instrument dependency
    configurations, including loading from YAML files, validating nodes.
    """

    def __init__(self):
        """Initialize DependencyConfig by loading all dependencies."""
        self._config = self._load_all_dependencies()

    @property
    def config(self) -> dict[tuple[str, str, str], list[DependencyNode]]:
        """Get the underlying dependency configuration dictionary.

        Returns
        -------
        dict[tuple[str, str, str], list[DependencyNode]]
            Mapping of ``(source, data_type, descriptor)`` tuples to lists of
            :class:`~.utils.DependencyNode` upstream dependency objects.
        """
        return self._config

    def inputs(self, key: tuple[str, str, str]) -> list[DependencyNode]:
        """Return upstream input nodes for the given job key.

        Parameters
        ----------
        key : tuple[str, str, str]
            ``(source, data_type, descriptor)`` identifying the downstream job.

        Returns
        -------
        list[DependencyNode]
            Upstream dependency nodes required as inputs for this job.

        Examples
        --------
        >>> reader = DependencyConfigReader()
        >>> reader.inputs(('glows', 'l1a', 'all'))
        [DependencyNode(source='leapseconds', ...), ...]
        """
        return self._config[key]["inputs"]

    def outputs(self, key: tuple[str, str, str]) -> list[DependencyNode]:
        """Return output product nodes produced by the given job key.

        Parameters
        ----------
        key : tuple[str, str, str]
            ``(source, data_type, descriptor)`` identifying the downstream job.

        Returns
        -------
        list[DependencyNode]
            Output product nodes for this job.

        Examples
        --------
        >>> reader = DependencyConfigReader()
        >>> reader.outputs(('glows', 'l1a', 'all'))
        [DependencyNode(source='glows', data_type='l1a', descriptor='de', ...), ...]
        """
        return self._config[key]["outputs"]

    def partition(self, key: tuple[str, str, str]) -> str | None:
        """Return the partition string for the given job key.

        Parameters
        ----------
        key : tuple[str, str, str]
            ``(source, data_type, descriptor)`` identifying the downstream job.

        Returns
        -------
        str | None
            Partition cadence string (e.g. ``'1d'``, ``'repoint'``) or ``None``.
        """
        return self._config[key]["partition"]

    def _load_all_dependencies(
        self,
    ) -> dict[tuple[str, str, str], list[DependencyNode]]:
        """Load all instrument YAML dependency files and unified dependency.

        Sets inputs and outputs to dictionaries where each key is a parent node
        (source, data_type, descriptor) representing a downstream product,
        and each value is a list of upstream :class:`~.utils.DependencyNode`
        objects.

        Raises
        ------
        FileNotFoundError
            If any expected YAML file is missing.
        ValueError
            If YAML content is invalid or empty.

        Examples
        --------
        >>> reader = DependencyConfigReader()
        >>> nodes = reader.inputs[('codice', 'l1a', 'all')]
        >>> nodes[0]
        DependencyNode(source='codice', data_type='l0', descriptor='raw', ...)
        """
        dependencies = {}
        yaml_dir = Path(__file__).parent

        for instrument in VALID_INSTRUMENTS:
            yaml_file = (
                yaml_dir / "dependencies" / f"imap_{instrument}_dependencies.yaml"
            )

            if instrument == "ialirt":
                continue

            if not yaml_file.exists():
                raise FileNotFoundError(
                    f"Dependency configuration file not found for '{instrument}' "
                    f"at {yaml_file}"
                )

            with open(yaml_file) as f:
                instrument_config = yaml.safe_load(f)

            if not instrument_config:
                raise ValueError(
                    f"Dependency content is empty for '{instrument}' in {yaml_file}"
                )

            # Parse YAML keys to construct (source, data_type, descriptor) tuples
            for key_str, value in instrument_config.items():
                # Skip any anchor definitions (common dependency groups).
                if not key_str.startswith("("):
                    continue

                try:
                    # Extract data_type and descriptor from key string
                    key_parts = key_str.strip("()").split(",")
                    data_type = key_parts[0].strip()
                    descriptor = key_parts[1].strip()
                    # Convert string key like "(l1a, all)" in the YAML to tuple
                    # (<instrument>,l1a, all) by combining with instrument source
                    # to get full downstream node.
                    # Validate the downstream product node by constructing a
                    # DependencyNode (validation runs in __post_init__).
                    DependencyNode(
                        source=instrument,
                        data_type=data_type,
                        descriptor=descriptor,
                    )
                    potential_job_node = (instrument, data_type, descriptor)

                    upstream_list = value["inputs"]
                    outputs_list = value.get("outputs") or []
                    flattened_upstream_deps = self.recursive_flatten_list(upstream_list)

                    upstream_deps_nodes = []
                    for upstream in flattened_upstream_deps:
                        upstream_deps_nodes.append(
                            DependencyNode(
                                source=upstream["source"],
                                data_type=upstream["data_type"],
                                descriptor=upstream["descriptor"],
                                required=upstream.get("required", True),
                                trigger_job=upstream.get("trigger_job", True),
                                dependency_query_time_range=upstream.get(
                                    "date_range", []
                                ),
                            )
                        )
                    job_outputs_list = []
                    for output in outputs_list:
                        job_outputs_list.append(
                            DependencyNode(
                                source=output["source"],
                                data_type=output["data_type"],
                                descriptor=output["descriptor"],
                                # NOTE: required flag is not supported yet.
                                # That's why it's default to false for outputs.
                                required=output.get("required", False),
                                trigger_job=output.get("trigger_job", True),
                                dependency_query_time_range=output.get(
                                    "date_range", []
                                ),
                            )
                        )

                    dependencies[potential_job_node] = {
                        "inputs": upstream_deps_nodes,
                        "outputs": job_outputs_list,
                        "partition": value.get("partition")
                    }

                except (ValueError, IndexError) as e:
                    raise ValueError(
                        f"Non-product key error: '{key_str}' in {yaml_file}: {e}"
                    ) from e

        return dependencies

    def recursive_flatten_list(self, nested_list):
        """Recursively flatten a nested list structure.

        Multiple inheritance in dependency YAML files can result in
        lists containing other lists, which this method flattens.

        For example:
        spice_basic: &spice_basic
            - source: leapseconds
                data_type: spice
                descriptor: historical
                trigger_job: false
            - source: spacecraft_clock
                data_type: spice
                descriptor: historical
                trigger_job: false

        spice_45sensor_l1b: &spice_45sensor_l1b
            - *spice_basic
            - source: imap_frames
                data_type: spice
                descriptor: historical
                trigger_job: false

        (l1b, 45sensor-de):
            inputs:
              - *spice_45sensor_l1b
              - source: hi
                  data_type: l1a
                  descriptor: 45sensor-de

        Parameters
        ----------
        nested_list : list
            A potentially nested list of dependencies.

        Returns
        -------
        list
            A single flattened list of dependencies.
        """
        flat_list = []
        for item in nested_list:
            if isinstance(item, list):
                # If the item is a list, extend with the flattened version of that list
                flat_list.extend(self.recursive_flatten_list(item))
            else:
                # Otherwise, append the item (which can be any object)
                flat_list.append(item)
        return flat_list


def upload_dependency_file(dependency_file_path: Path, serialized_dependencies: str):
    """Upload a JSON file containing a job's dependencies to S3.

    Parameters
    ----------
    dependency_file_path : Path
        The dependency JSON file to upload.
    serialized_dependencies : str
        The serialized upstream dependencies to upload.
    """
    # Check if the file already exists
    if os.path.isfile(dependency_file_path):
        raise KeyError(
            f"{dependency_file_path} already exists, cannot create JSON file."
        )
    # call the upload API handler directly
    signed_url = upload_api.lambda_handler(
        {
            "pathParameters": {"proxy": dependency_file_path.as_posix()},
            "requestContext": {
                "authorizer": {"lambda": {"scope": "write", "apiKey": "batch-starter"}}
            },
        },
        None,
    )
    if signed_url["statusCode"] == 409:
        logger.info(
            f"Dependency file already exists in S3: {dependency_file_path}. Reusing"
            f"file."
        )
        return {"statusCode": 200, "body": signed_url["body"]}
    elif signed_url["statusCode"] != 200:
        logger.error(
            f"Failed to get S3 pre-signed URL for file: {dependency_file_path}. "
            f"As a result, failed to kick off job. "
            f"Error message: {signed_url['body']}, "
            f"with status code: {signed_url['statusCode']}."
        )
        return None
    try:
        response = requests.put(
            signed_url["body"].strip('"'),
            data=serialized_dependencies,
            headers={"Content-Type": "application/json"},
            timeout=60.0,
        )
        logger.info(
            f"Dependency file uploaded successfully to s3 with status code: "
            f"{response.status_code}"
        )
        return response
    except Exception as e:
        logger.error(
            f"Unexpected error during cadence file upload: {e}. "
            f"Dependency file upload failed and the job did not get kicked off."
        )
        return None
