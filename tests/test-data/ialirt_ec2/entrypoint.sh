#!/bin/bash

# Mount the S3 bucket
/app/mount_s3.sh

# Start the Flask application on multiple ports
/app/start_flask.sh
