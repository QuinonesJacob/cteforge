# src/cteforge/trust/

Pure policy: trust labels for results, and who may do what (DESIGN §8, §18; ADR 0007, 0018).
No I/O. Project root: ../../../CLAUDE.md

## What belongs here (authoritative)
- `labels.py` — result trust labels (DESIGN §8).
- `approval.py` — permission and approval policy: role checks per action, auto-apply rule,
  who may approve/verify.

If something does not clearly fit one of the above, it does not belong here.
"Not forbidden" is not the same as "allowed."

## Public API
```python
# labels.py
class TrustInputs(BaseModel):        # frozen
    definition_states: list[DefinitionState]   # one per definition used; [] for raw SQL
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

# approval.py
class Action(StrEnum): SEARCH, DESCRIBE, RUN, PROPOSE, APPROVE, VERIFY, MANAGE_USERS, MANAGE_SETTINGS
def allowed(user: User, action: Action) -> bool        # role minimums per ADR 0018
def can_approve(user: User, target: Definition | None, *, author: str | None = None) -> bool
    # approver/admin, or the target definition's owner; humans only for verified targets;
    # four-eyes: not the proposal's `author` unless a human admin (author=None skips the rule)
def can_verify(user: User, target: Definition | None, *, author: str | None = None) -> bool
    # humans only (ADR 0007), approver/admin only (ADR 0018); owners may NOT verify;
    # four-eyes: not the version's `author` unless a human admin (author=None skips the rule)
def can_auto_apply(mode: TeamMode, target: Definition | None, checks: list[Issue]) -> bool
    # DESIGN §18: open mode, no error issues, no checks_incomplete, target new or not verified
```
Label rules (DESIGN §8): start at `verified`; `has_select` caps at `built_from_verified_parts`;
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
