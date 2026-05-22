#!/bin/bash

# Navigate to the script's directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=================================================="
echo "Starting News Aggregator Daemon for Prediction Markets"
echo "=================================================="
echo "Timestamp: $(date)"

# Check if venv exists, if not create it
if [ ! -d "venv" ]; then
    echo "Virtual environment not found. Creating 'venv'..."
    python3 -m venv venv
    ./venv/bin/pip install -r requirements.txt
fi

# Run the aggregator in daemon mode
echo "Running aggregator..."
./venv/bin/python main.py --daemon --output news_database.json
