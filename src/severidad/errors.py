"""Errors raised by the package.

They subclass the built-in errors that the package raised before (``ValueError`` and ``OSError``),
so existing ``except`` clauses keep working, and callers can now tell bad data from a failed
source with one ``except`` each.
"""
from __future__ import annotations


class SeveridadError(Exception):
    """Base class of every error raised on purpose by this package."""


class DataError(SeveridadError, ValueError):
    """Input data is missing, inconsistent or not the published file."""


class SourceError(SeveridadError, OSError):
    """A remote source (STAC catalogue, municipal boundaries service) failed or answered badly."""
