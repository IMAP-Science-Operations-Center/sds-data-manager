import json
import os
import requests
import time
from jose import jwk, jwt
from jose.utils import base64url_decode

def verify_cognito_token(token):
    
    '''
     This function verifies that a cognito token that we've received is:
         - Actually from the expected userpool
         - Not expired
         - From the correct cognito client ID
    '''

    region = 'us-west-2'
    userpool_id = os.environ["COGNITO_USERPOOL_ID"]
    app_client_id = os.environ["COGNITO_APP_ID"]
    keys_url = f'https://cognito-idp.{region}.amazonaws.com/{userpool_id}/.well-known/jwks.json'
    response = requests.get(keys_url)
    keys = (response.json())['keys']
    
    headers=jwt.get_unverified_headers(token)
    kid=headers['kid']
    key_index = -1
    for i in range(len(keys)):
        if kid == keys[i]['kid']:
            key_index = i
            break
    if key_index == -1:
        print('Public key not found in jwks.json')
        return False
    # construct the public key
    public_key = jwk.construct(keys[key_index])
    # get the last two sections of the token,
    # message and signature (encoded in base64)
    message, encoded_signature = str(token).rsplit('.', 1)
    # decode the signature
    decoded_signature = base64url_decode(encoded_signature.encode('utf-8'))
    # verify the signature
    if not public_key.verify(message.encode("utf8"), decoded_signature):
        print('Signature verification failed')
        return False
    print('Signature successfully verified')
    # since we passed the verification, we can now safely
    # use the unverified claims
    claims = jwt.get_unverified_claims(token)
    # additionally we can verify the token expiration
    if time.time() > claims['exp']:
        print('Token is expired')
        return False
    # and the Audience  (use claims['client_id'] if verifying an access token)
    if claims['client_id'] != app_client_id:
        print('Token was not issued for this audience')
        return False
    # now we can use the claims
    print(claims)
    return claims