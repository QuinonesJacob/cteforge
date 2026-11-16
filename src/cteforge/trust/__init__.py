"""Trust-label and approval policy (DESIGN §8, §18). Pure: no I/O."""

from cteforge.trust.approval import (
    Action,
    allowed,
    can_approve,
    can_auto_apply,
    can_verify,
)
from cteforge.trust.labels import TrustInputs, TrustResult, assess

__all__ = [
    "Action",
    "TrustInputs",
    "TrustResult",
    "allowed",
    "assess",
    "can_approve",
    "can_auto_apply",
    "can_verify",
]
