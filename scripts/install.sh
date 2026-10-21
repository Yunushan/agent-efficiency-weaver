#!/usr/bin/env sh
set -eu
python3 -m pip install -e .
python3 -m agentweaver validate
