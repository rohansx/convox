"""Test-wide defaults: tests that build dev-env Settings must not write the app log
(logs/convox.log) or call recordings (out/calls) into the repo. Tests of those features
set the values explicitly."""

import os

os.environ.setdefault("LOG_FILE", "")
os.environ.setdefault("CALL_RECORDING", "false")
