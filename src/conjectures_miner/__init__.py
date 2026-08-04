"""Miner-side CLI for the conjectures.io Subnet 66 Lean-proof validator."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("conjectures-miner")
except PackageNotFoundError:  # a source tree that was never installed
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
