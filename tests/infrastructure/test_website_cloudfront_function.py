"""Cloudfront function for URL routing."""

import subprocess
import tempfile
from pathlib import Path

import pytest


def test_cloudfront_function():
    """Testing the CF Function routing.

    The actual test is defined in the test_uri_routes.js file. We run it as
    a subprocess through pytest so we can get that report here easily. We know
    that to install the cdk node must be installed, so we can run `node file.js`
    within the test suite as well.
    """
    test_dir = (
        Path(__file__).parent.parent.parent / "sds_data_manager/cloudfront_functions"
    )

    # Copy everything over to a new temporary directory that gets
    # cleaned up automatically for us. We need to do this because
    # CloudFront doesn't allow us to "export" the function, so we
    # monkeypatch the export statement in here for testing purposes.
    with tempfile.TemporaryDirectory() as tmpdirname:
        tmpdirpath = Path(tmpdirname)
        for orig_file in test_dir.glob("*"):
            with open(orig_file) as fin, open(tmpdirpath / orig_file.name, "w") as fout:
                for line in fin:
                    if line.startswith("function"):
                        # prepend export
                        line = "export " + line  # noqa
                    fout.write(line)

        test_file = tmpdirpath / "test_uri_routes.js"
        try:
            subprocess.run(
                ["node", str(test_file.resolve())],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as err:
            pytest.fail(f"CloudFront routes don't align\n{err.stderr}")
