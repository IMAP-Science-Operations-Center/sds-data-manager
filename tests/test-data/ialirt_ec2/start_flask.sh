#!/bin/bash

# Define the ports on which the Flask app should run
PORTS=(80 8080)

# Loop through the ports and start a Flask instance for each
for port in "${PORTS[@]}"; do
    echo "Starting Flask app on port $port"
    FLASK_PORT=$port python3 /app/test_app.py &
done

# Wait for all Flask instances to complete
wait
