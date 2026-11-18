# 0007 — Trust states; humans approve changes to verified definitions

Status: accepted · 2026-11-15

## Decision
States draft → llm_annotated → verified. Teams choose strict (verified only) or open (llm_annotated usable, labeled). LLMs may create and improve unverified definitions; changes to verified definitions always need human approval.

## Consequences
- Every result carries a trust label.
- Guide, don't restrict, by default; lockable for cautious teams.

## Amendment (2026-11-17)
ADR 0027 (accepted, not built): agent-kind users holding the **admin** role may verify;
four-eyes still applies with no admin exemption for agents.
