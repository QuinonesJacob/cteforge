"""Use-case: the full card of one definition, and its version history (DESIGN §6, §15).

Needs Action.DESCRIBE (viewer). Any state can be described, drafts included: describing
is how a person or agent learns why something is not usable. Audit is best-effort here
(`fail_closed=False`): a lookup returns no warehouse data.
"""

from __future__ import annotations

from typing import NoReturn

from cteforge.domain.audit import AuditKind
from cteforge.domain.errors import NotFoundError
from cteforge.domain.models import DefinitionVersionInfo
from cteforge.domain.users import User
from cteforge.services import _audit
from cteforge.services.auth import require
from cteforge.services.cards import DefinitionCard, card_from_definition
from cteforge.services.context import AppContext
from cteforge.trust.approval import Action


def _require(ctx: AppContext, user: User, what: str) -> None:
    with _audit.audit_failures(ctx, user, AuditKind.GET_DEFINITION, extra={"op": what}):
        require(user, Action.DESCRIBE)


def _not_found(ctx: AppContext, user: User, id: str, version: int | None, what: str) -> NoReturn:
    _audit.record(
        ctx,
        user,
        AuditKind.GET_DEFINITION,
        fail_closed=False,
        error="NotFoundError",
        extra={"op": what, "id": id, "version": version},
    )
    suffix = f" version {version}" if version is not None else ""
    raise NotFoundError(f"Definition '{id}'{suffix} does not exist")


def get_definition(
    ctx: AppContext, user: User, id: str, version: int | None = None
) -> DefinitionCard:
    """The definition card (latest version unless `version`), with its full history.
    Raises NotFoundError for an unknown id or version."""
    _require(ctx, user, "get_definition")
    definition = ctx.store.get_definition(id, version)
    if definition is None:
        _not_found(ctx, user, id, version, "get_definition")
    card = card_from_definition(definition, ctx.store.definition_history(id))
    _audit.record(
        ctx,
        user,
        AuditKind.GET_DEFINITION,
        fail_closed=False,
        definitions=[f"{definition.id}@{definition.version}"],
        extra={"op": "get_definition"},
    )
    return card


def definition_history(ctx: AppContext, user: User, id: str) -> list[DefinitionVersionInfo]:
    """Every version of `id`, oldest first. Raises NotFoundError for an unknown id."""
    _require(ctx, user, "definition_history")
    history = ctx.store.definition_history(id)
    if not history:
        _not_found(ctx, user, id, None, "definition_history")
    _audit.record(
        ctx,
        user,
        AuditKind.GET_DEFINITION,
        fail_closed=False,
        definitions=[f"{h.id}@{h.version}" for h in history[-1:]],
        extra={"op": "definition_history"},
    )
    return history
