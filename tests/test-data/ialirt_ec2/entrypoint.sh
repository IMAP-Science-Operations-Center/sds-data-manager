#!/bin/bash

# Mount the S3 bucket
/app/mount_s3.sh

# Start the Flask application
/app/start_flask.sh
