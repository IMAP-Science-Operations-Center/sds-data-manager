import os
import re
import boto3
import time
import json
from dagster import (
    EventRecordsFilter,
    DagsterEventType,
    AssetKey,
    MaterializeResult,
    AssetSelection,
    AutomationCondition,
    AssetMaterialization
)
import imap_data_access
from imap_data_access.io import download
from sds_data_manager.lambda_code.SDSCode.database import models

def _existing_asset(context,
                    asset_key,
                    partition,
                    file_names):
    '''
    This checks the most recent materialization of an asset, if it exists. 

    If an asset already exists and the file_names in the metadata are the same between the two, 
    then we return True. 

    Otherwise we return False. 
    '''
    records = context.instance.get_event_records(
                        EventRecordsFilter(
                            asset_key=AssetKey([asset_key]), 
                            asset_partitions=[partition],
                            event_type=DagsterEventType.ASSET_MATERIALIZATION
                        ),
                        limit=1
                    )
    
    if records:
        # Extract the previous file list from the metadata
        last_metadata = records[0].asset_materialization.metadata
        last_files_used = last_metadata.get("file_names").value
        
        # Compare lists
        if set(last_files_used) == set(file_names):
            return True
    return False

def get_materialization(context,
                        asset_key,
                        partition,
                        file_names,
                        version,
                        data_type):
    
    if _existing_asset(context, asset_key, partition, file_names):
        return
        
    return AssetMaterialization(
                asset_key=AssetKey([asset_key]),
                partition=str(partition),
                metadata={
                    "file_names": file_names,
                    "input_type": data_type,
                    "version": version
                }
            )

def get_materialization_result(context,
                                asset_key: str,
                                partition: str | None,
                                file_names: list[str],
                                versions: list[str],
                                data_type: str,
                                inputs: dict = {}) -> MaterializeResult | None:
    '''
    This provides a common method to materialize an asset. 

    We first check if an asset already exists. If it does, we return nothing. 

    data_type must be one of "science", "ancillary", "spice", "spin", or "repoint". 
    '''
    asset_key = asset_key.replace("-", "")
    if _existing_asset(context, asset_key, partition, file_names):
        return
        
    return MaterializeResult(
                asset_key=AssetKey(asset_key),
                metadata={
                    "file_names": file_names,
                    "input_type": data_type,
                    "version": versions,
                    "inputs": inputs
                }
            )
    
def check_already_processed_science_file(context,
                                         session,
                                         dependency,
                                         instrument,
                                         level,
                                         descriptor,
                                         job_command,
                                         start_date,
                                         repoint):
    '''
    This function will check if the latest file at the SDC has already been processed using 
    the gathered dependencies. To do this, it:

    1) Finds the latest file
    2) Finds the corresponding Processing Job
    3) Read in the .json dependency file from the corresponding Processing Job
    4) Compares the Ancillary and Science files in the dependency file to the current ones 
    5) If they all match, the function returns the materialization result of the latest file

    Yes, this is somewhat complicated right now. I think the best way going forward would be to 
    add the dependencies to a particular file either in the ScienceFiles or ProcessingJob tables. 
    '''
    if repoint:
        latest_file = session.query(models.ScienceFiles).filter(
                                        models.ScienceFiles.instrument == instrument,
                                        models.ScienceFiles.data_level == level,
                                        models.ScienceFiles.descriptor == descriptor,
                                        models.ScienceFiles.repointing == int(repoint)
                                    ).order_by(models.ScienceFiles.version.desc()).first()
    else:
        latest_file = session.query(models.ScienceFiles).filter(
                                        models.ScienceFiles.instrument == instrument,
                                        models.ScienceFiles.data_level == level,
                                        models.ScienceFiles.descriptor == descriptor,
                                        models.ScienceFiles.start_date == start_date
                                    ).order_by(models.ScienceFiles.version.desc()).first()
    if latest_file:
        max_version_record = (
            session.query(models.ProcessingJob)
            .filter(models.ProcessingJob.instrument == latest_file.instrument,
                models.ProcessingJob.data_level == latest_file.data_level,
                models.ProcessingJob.descriptor == job_command,
                models.ProcessingJob.start_date == latest_file.start_date,
                models.ProcessingJob.status.in_(
                        [models.Status.INPROGRESS.value, models.Status.SUCCEEDED.value]
                    ))
            .order_by(models.ProcessingJob.version.desc())
            .first()
        )
        if max_version_record:
            match = re.search(r'--dependency\s+(\S+\.json)', max_version_record.container_command)
            if match:
                dependency_file = match.group(1)
                print(dependency_file)
                context.log.info(dependency_file)
                SSM_CLIENT = boto3.client("ssm")
                ssm_parameter_name = os.environ.get(
                    "SSM_API_KEY_PARAMETER", "/imap-sdc/batch-jobs/api-key"
                )
                try:
                    ssm_response = SSM_CLIENT.get_parameter(
                        Name=ssm_parameter_name, WithDecryption=True
                    )
                    imap_data_access.config["API_KEY"] = ssm_response["Parameter"]["Value"]
                except Exception as e:
                    print(f"Could not retrieve API key from SSM: {e}")
                dependency_filepath = download(dependency_file)
                with open(dependency_filepath) as f:
                    old_inputs = json.loads(f.read())

                dependencies_match = True
                old_science_inputs = []
                old_ancillary_inputs = []
                new_science_inputs = []
                new_ancillary_inputs = []
                for dep in old_inputs:
                    if dep['type'] == "science":
                        old_science_inputs.extend(dep['files'])
                    if dep['type'] == "ancillary":
                        old_ancillary_inputs.extend(dep['files'])
                new_inputs = json.loads(dependency.serialize())
                for dep in new_inputs:
                    if dep['type'] == "science":
                        new_science_inputs.extend(dep['files'])
                    if dep['type'] == "ancillary":
                        new_ancillary_inputs.extend(dep['files'])

                if set(old_science_inputs) != set(new_science_inputs):
                    dependencies_match = False
                if set(old_ancillary_inputs) != set(new_ancillary_inputs):
                    dependencies_match = False

                if dependencies_match:
                    # The latest file does not need to be updated. 
                    # We need to tell dagster that this asset is complete. 
                    context.log.info(f"Latest file: {latest_file.file_path}")
                    context.log.info(f"Version: {latest_file.version}")
                    return latest_file

