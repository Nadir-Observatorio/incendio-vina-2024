"""Burn severity from two Sentinel-2 L2A scenes (dNBR with offset, Key and Benson classes).

Minimal, self-contained implementation used by the notebooks of this repository. It follows the
published method (Key and Benson 2006; Parks et al. 2018) and adds one check that matters in
practice: the reflectance offset is measured on water pixels instead of trusted from the catalogue
metadata (see README, "Limitations", O4).
"""
from __future__ import annotations

__version__ = "1.0.0"
