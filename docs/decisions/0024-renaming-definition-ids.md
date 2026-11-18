# 0024 — Definition IDs can be renamed, deliberately

Status: accepted · 2026-11-17

## Context
The ID (slug) is filled in from the title on import. A permanent ID is too restrictive:
names drift, and an ID agents don't find is a sign it should change. But other things
point at an ID: recipes, compositions, pending proposals, the audit log, and references
outside cteforge (agent instructions, scripts) that cteforge can't see.

## Decision
- **On import** the ID is filled in from the title and locked; "Edit ID" unlocks it.
- **After creation** the ID can be renamed by a deliberate action. Before it applies, the
  person sees every place inside cteforge that uses the ID (recipes, pending proposals,
  joins/compositions that name it) and a warning that references outside cteforge
  (agent prompts, scripts) will stop working; they confirm by typing the new ID.
- A rename is a proposal like any other change: on a verified definition it needs approval
  (four-eyes, ADR 0018). Agents may propose renames; a person approves.
- On apply, references inside cteforge (recipes, pending proposals) are updated to the new
  ID in the same transaction; the audit log keeps history under the old ID and records the
  rename.
- **No silent alias.** The old ID stops working as an ID, but:
  - former IDs go into the definition's search document (ADR 0023), so searching the old
    name finds the new definition;
  - using a former ID returns a not-found error that names the new ID, so an agent with
    stale instructions corrects itself in one step.
- A former ID can't be reused by another definition (that error would point at the wrong
  thing).

## Consequences
- Names can improve over time; every break is visible and self-correcting rather than
  silent.

## Addendum (2026-11-17): implementation choices
- **A rename is its own proposal kind**, `rename_definition` (payload `from_id`, `to_id`
  and the references seen when proposed, so the reviewer sees what the proposer saw). Same
  policy as any change to the definition: auto-applied in open mode when the definition is
  not verified; otherwise it waits; a verified one needs a human approver; four-eyes
  applies; it can never be approved "with verify" (nothing to verify). Agents propose
  through `revise_definition(new_id=...)` (MCP; nothing else may change in that call), so
  the tool count stays 12. HTTP: `POST /proposals/definitions/{id}/rename`; CLI
  `cteforge rename OLD NEW`.
- **No new version.** A rename changes the name, not the definition: version numbers,
  states, verification, authors and change notes stay as they are. A version bump would
  have made every pinned recipe and pending revision stale, and (as a metadata-only
  revision of a verified definition) dropped its verification. The rename's record is the
  former-ID row (who, when, proposal) and the audit event.
- **Stored version YAML is rewritten** to the new ID, so history shows one ID and a
  definition read from any version is self-consistent. The audit log is history and is
  never rewritten (events keep the old ID; the rename event lists both `old@v` and `new@v`).
- **Every stored copy moves in one store transaction** (`Store.rename_definition`, after
  the usual compare-and-swap claim of the proposal): the `definitions` row, every version,
  every recipe version that uses it (pins kept), every proposal that targets or uses it
  (pending and decided; definition YAML in payloads too), and its search vectors are
  deleted (the search document names the ID; they are re-embedded). Join-map edges name
  tables, not definitions, so they don't change.
- **Pending proposals are updated, not made stale**: their content is unchanged and their
  base version still holds, so a revision proposed against the old ID applies under the new
  one. The exception is another pending **rename** of the same definition: it keeps the
  `from_id` it asked for, and applying it after the first rename is refused as stale
  ("renamed since; propose again from the new ID").
- **Former IDs** live in a `former_ids` table (`create_all`, no migration): former id →
  current id (rewritten on each later rename, so a chain a → b → c resolves in one
  lookup), `renamed_to`, `renamed_by`, `renamed_at`, `proposal_id`. Every lookup by id goes
  through one helper (`services/_library.definition_not_found`), so describe, history,
  search document, references, run (also pinned and recipe uses), verify, revise, rename
  and revalidate all fail with "Definition 'a' was renamed to 'c' on <date>; use the new
  ID." Former IDs are in the search document ("Formerly `a`.") and a keyword field.
- **Reuse:** a former ID can't become another definition's ID (new or by rename). A
  definition may take back one of its **own** former IDs (undoing a rename); that row is
  then removed, since the ID is current again.
