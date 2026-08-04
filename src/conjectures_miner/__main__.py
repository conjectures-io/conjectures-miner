"""Support `python -m conjectures_miner` alongside the installed `conjectures` script."""

import sys

from conjectures_miner.cli import run

if __name__ == "__main__":
    sys.exit(run())
