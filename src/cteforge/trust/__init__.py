"""Trust-label and approval policy (DESIGN §8, §18). Pure: no I/O."""

from cteforge.trust.approval import (
    allowed,
    can_approve,
    can_auto_apply,
    can_verify,
    effective_tools,
    tools_of,
)
from cteforge.trust.labels import TrustInputs, TrustResult, assess

__all__ = [
    "TrustInputs",
    "TrustResult",
    "allowed",
    "assess",
    "can_approve",
    "can_auto_apply",
    "can_verify",
    "effective_tools",
    "tools_of",
]
