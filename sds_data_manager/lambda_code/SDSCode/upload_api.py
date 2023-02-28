import json
import os
import boto3
import cognito_utils

def _load_allowed_filenames():
    '''
    This function loads the config.json file, and stores the contents as a python dictionary 
    
    :return: dictionary object of file types and their attributes. 
    '''
    current_dir = os.path.dirname(__file__)
    config_file = os.path.join(current_dir, "config.json")
    
    with open(config_file) as f:
        data = json.load(f)
    return data

def _check_for_matching_filetype(pattern, filename):
    '''
    This function takes in a pattern from config.json and compares it to the desired file name

    :param pattern: A file naming pattern from the config.json
    :param filename: Required.  String name of the desired file name.  
    
    :return: None if there is no match, or the file_dictionary if there is.  
    '''
    split_filename = filename.replace("_", ".").split(".")

    if len(split_filename) != len(pattern):
        return None
    
    i = 0
    file_dictionary = {}
    for field in pattern:
        if pattern[field] == '*':
            file_dictionary[field] = split_filename[i]
        elif pattern[field] == split_filename[i]:
            file_dictionary[field] = split_filename[i]
        else:
            return None
        i += 1
    
    return file_dictionary

def _generate_signed_upload_url(filename, tags={}):
    '''
    Based on a given filename, this function will open up a presigned url into the correct location on the SDS storage bucket
    
    :param filename: Required.  A string representing the name of the object to upload.  
    :param tags: Optional.  A dictionary object of key:value pairs that will be stored in the S3 object metadata.  

    :return: None if the filename does not match mission naming conventions.  Otherwise, a URL string.  
     '''
    filetypes = _load_allowed_filenames()
    for filetype in filetypes:
        path_to_upload_file = filetype['path']
        metadata = _check_for_matching_filetype(filetype['pattern'], filename)
        if metadata is not None:
            break
        
    if metadata is None:
        logger.info(f"Found no matching file types to index this file against.")
        return None
    
    bucket_name = os.environ["S3_BUCKET"]
    url = boto3.client('s3').generate_presigned_url(ClientMethod='put_object', 
                                                    Params={'Bucket': bucket_name[5:], 
                                                    'Key': path_to_upload_file+filename, 
                                                    'Metadata': tags}, 
                                                    ExpiresIn=3600)
    
    return url

def lambda_handler(event, context):
    '''
    The entry point to the upload API lambda.  
    This function returns an S3 signed-URL based on the input filename, which the user can 
    then use to load a file onto the SDS. 
    
    :param event: Dictionary object.  
                  Specifically only requires event['queryStringParameters']['filename'] be present. 
                  User-specified key:value pairs can also exist in the 'queryStringParameters', storing these pairs as object metadata.  
    :param context: Unused
    
    :return: If all checks are successful, this returns a pre-signed url where users can upload a data file to the SDS.  
    '''
    verified_token = False
    try:
        token=event["headers"]["authorization"]
        verified_token = cognito_utils.verify_cognito_token(token)
    except Exception as e:
        logger.info(f"Authentication error: {e}")


    if not verified_token:
        logger.info("Supplied token could not be verified")
        return {
                'statusCode': 400,
                'body': json.dumps("Supplied token could not be verified")
            }

    if 'filename' not in event['queryStringParameters']:
        return {
            'statusCode': 400,
            'body': json.dumps("Please specify a filename to upload")
        }
        
    filename = event['queryStringParameters']['filename']
    url = _generate_signed_upload_url(filename, tags=event['queryStringParameters'])
    
    if url is None:
        return {
            'statusCode': 400,
            'body': json.dumps("A pre-signed URL could not be generated. Please ensure that the file name matches mission file naming conventions.")
        }
    
        
    return {
        'statusCode': 200,
        'body': json.dumps(url)
    }
