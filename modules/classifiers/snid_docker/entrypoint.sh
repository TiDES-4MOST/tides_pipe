#!/bin/bash
set -e

# Create and fix ownership of mounted volume if it exists
if [ -d "/snid_api_runs" ]; then
    chown -R sniduser:snidgroup /snid_api_runs
fi

# Execute the main container command (uvicorn)
exec "$@"

