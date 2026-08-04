"""Miner-side CLI for the conjectures.io Subnet 66 paid Lean-proof validator.

Nothing here imports the validator. The two byte-exact contracts with it -- the submission
bundle format and the canonical request digest -- live in `bundle` and `digest`, both
stdlib-only and both covered by golden vectors, so drift shows up as a failing test rather
than as a `SIGNATURE_INVALID` after a miner has already paid.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("conjectures-miner")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
