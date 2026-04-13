"""
Trail Agent — standalone trail encoder/decoder/executor.

Extracted from holodeck-studio into a self-contained CLI agent.
Zero external dependencies — uses only Python stdlib.

Usage:
    python -m trail_agent encode <worklog.json> -o output.bin
    python -m trail_agent decode <trail.bin> --format verbose
    python -m trail_agent verify <trail.bin>
    python -m trail_agent execute <trail.bin> --world mock
    python -m trail_agent compile <entries.json>
    python -m trail_agent disassemble <trail.bin>
    python -m trail_agent onboard
    python -m trail_agent status
"""

import sys
import os

# Ensure the agent root is on the path for local imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cli import main


if __name__ == "__main__":
    sys.exit(main())
