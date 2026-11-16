"""Composition (M2, DESIGN §17): combine rendered definitions and a final SELECT."""

from cteforge.compose.assemble import ComposedQuery, Part, compose
from cteforge.compose.checks import JoinUse

__all__ = ["ComposedQuery", "JoinUse", "Part", "compose"]
