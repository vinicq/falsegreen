"""falsegreen: find unit tests that give false positives."""
from .scanner import run, main, CASES, Finding

__version__ = "0.2.1"
__all__ = ["run", "main", "CASES", "Finding", "__version__"]
