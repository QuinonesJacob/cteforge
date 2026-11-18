# src/cteforge/trust/

Pure policy: trust labels for results, and who may do what (DESIGN §8, §9, §18; ADR 0007,
0018, 0027).
No I/O. Project root: ../../../CLAUDE.md

## What belongs here (authoritative)
- `labels.py` — result trust labels (DESIGN §8).
- `approval.py` — permission and approval policy: effective tools (role ∩ token), tool
  membership, auto-apply rule, who may approve/verify, and the user/role/token rules for
  admin agents (ADR 0027).

If something does not clearly fit one of the above, it does not belong here.
"Not forbidden" is not the same as "allowed."

## Public API
```python
# labels.py
class TrustInputs(BaseModel):        # frozen
    definition_states: list[DefinitionState]   # one per SQL definition used; [] for raw SQL
    composed_states: list[DefinitionState] = [] # ADR 0026: own state of each composed definition used
    raw_sql: bool = False                      # check_and_run
    has_select: bool = False                   # an agent-written final SELECT
    joins_known: bool = True                   # every join in agent-written SQL is known
    issues: list[Issue] = []                   # from render/compose
    mode: TeamMode = TeamMode.OPEN
class TrustResult(BaseModel):        # frozen
    label: TrustLabel
    reasons: list[str]                         # plain language, one per rule that applied
    usable: bool                               # False -> services refuse to run it
    blocking: list[Issue]                      # draft_not_usable / strict_mode_blocked
def assess(inputs: TrustInputs) -> TrustResult

# approval.py — effective tools (ADR 0027 addendum)
def role_ceiling(role: Role) -> frozenset[Tool]     # admin: every tool; others: allowed − ADMIN_ONLY_TOOLS
def role_tools(role, kind, *, defaults_only=False) -> frozenset[Tool]   # no token: allowed (CLI) / defaults (stdio)
def effective_tools(role: Role, token: TokenInfo, kind: ActorKind) -> frozenset[Tool]
    # ceiling ∩ (follow_role: (defaults ∪ include) − exclude; only: include);
    # minus verify for an agent whose role is not admin
def check_role(role: Role) -> list[str]             # admin-only tools elsewhere, admin not all, defaults ⊄ allowed
# the acting user
def tools_of(user) -> frozenset[Tool]               # user.tools, else BUILTIN_ROLES[user.role] defaults, else {}
def reviews_all(user) -> bool; is_admin(user); is_agent(user)
def allowed(user: User, tool: Tool) -> bool         # active, tool in tools_of; verify never for a non-admin agent
def may_change_verified(user) -> bool                # human, or an agent with the admin role
def can_approve(user: User, target: Definition | None, *, author: str | None = None) -> bool
    # review_proposal, and review_all or owner of target; VERIFIED target: may_change_verified;
    # four-eyes: not the proposal's `author` unless a HUMAN admin (author=None skips the rule)
def can_verify(user: User, target: Definition | None, *, author: str | None = None) -> bool
    # the verify tool (agents: admin role only); owners may NOT verify;
    # four-eyes: not the version's `author` unless a HUMAN admin (no agent exemption)
# users, roles, tokens (admin agents = agent kind + admin role)
def can_manage_user(actor, target) -> bool          # manage_user; agents never an admin user (itself included)
def can_grant_role(actor, role_name) -> bool        # manage_user; only humans grant admin
def can_edit_role(actor, role_name) -> bool         # manage_role; only humans edit admin
def can_mint_token(actor, owner) -> bool            # never an agent; own, or manage_user for anyone
def can_revoke_token(actor, owner) -> bool          # own; else can_manage_user
def can_set_token_tools(actor, owner) -> bool       # own; else can_manage_user
def can_auto_apply(mode: TeamMode, target: Definition | None, checks: list[Issue]) -> bool
    # DESIGN §18: open mode, no error issues, no checks_incomplete, target new or not verified
def join_may_auto_apply(action: str, author_kind: ActorKind) -> bool   # ADR 0026: never an agent's "add"
def confirms_join(decider_kind: ActorKind, *, auto: bool) -> bool      # only a person's approval
```
Label rules (DESIGN §8): start at `verified`; `has_select` caps at `built_from_verified_parts`;
a composed definition whose own state is not verified does the same (and its unknown joins
→ `exploratory` like a select's); a verified one adds nothing (its parts' states still
count);
any `llm_annotated` definition caps at `llm_annotated`; raw SQL → `new_logic_on_known_joins`
if `joins_known` else `exploratory`; `unknown_join` issue or `joins_known=False` with a
select → `exploratory`; each of `fixed_override`, `value_not_allowed`, `transpiled` lowers
one step along `TRUST_ORDER` (floor `exploratory`). Any draft → usable False
(`draft_not_usable`). Strict mode → usable only for `verified` / `built_from_verified_parts` reached with no
downgrade issue; any `fixed_override` / `value_not_allowed` / `transpiled` blocks it
(`strict_mode_blocked`), since the SQL run is not what was verified.

## Common misplacements
- Definition state transitions persisted to a database — store/ via services/.
- Authentication (tokens) — services/.

## Interactions
Imports domain only. Imported by services.

## If you need to add something not on the allowlist
STOP. Do not place the file here on the assumption it kind of fits. Surface the case to
the user, propose the addition, and update this CLAUDE.md (and the root CLAUDE.md if the
change is project-wide) before placing the new code. The architecture evolves through
conscious decisions, not silent drift.
