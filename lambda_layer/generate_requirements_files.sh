#!/bin/bash

poetry export -f requirements.txt --with layer-database -o lambda_layer/database/requirements.txt --without-urls
poetry export -f requirements.txt --with layer-processing -o lambda_layer/processing/requirements.txt --without-urls
poetry export -f requirements.txt --with layer-spice -o lambda_layer/spice/requirements.txt --without-urls

echo "Requirements files generated successfully."