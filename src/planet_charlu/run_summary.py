"""Compatibility import for the extracted summary implementation."""
import sys
from spbazaar_runlog import run_summary as _implementation

sys.modules[__name__] = _implementation
