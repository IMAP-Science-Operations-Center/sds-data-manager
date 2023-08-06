#!/usr/bin/env python3

from imap_processing.swe import decom_swe


def handler(event, context):
    """This is a dummy code that runs decom code using SWE test
    data in pip package.

    Parameters
    ----------
    event : Dict
        AWS lambda event dictionary
    context : LambdaContext
        AWS lambda context object. This object is passed to all
        lambda functions. See:
        https://docs.aws.amazon.com/lambda/latest/dg/python-context-object.html

    Returns
    -------
    Dict
        status : str
            SUCCEEDED or FAILED.
    """
    instrument = event.get("instrument", "swe")
    instrument_list = [
        "swe",
        "swapi",
        "mag",
        "glows",
        "codice",
        "imap-hi",
        "imap-lo",
        "imap-ultra",
        "hit",
        "idex",
    ]

    if instrument in instrument_list and instrument != "swe":
        print(f"{instrument} not supported")
        return {"status": "FAILED"}

    # Grabing test data from pip package path
    # This path was looked up by running `pip show imap-processing`
    pip_path = "/var/lang/lib/python3.11/site-packages"
    data_file = "science_block_20221116_163611Z_idle.bin"
    data_file = f"{pip_path}/imap_processing/swe/tests/{data_file}"
    decom_data = decom_swe.decom_packets(data_file)
    print(f"Decom data: {decom_data[0].header}")
    return {"status": "SUCCEEDED"}
