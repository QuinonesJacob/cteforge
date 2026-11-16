# 0007 — Trust states; humans approve changes to verified definitions

Status: accepted · 2026-11-15

## Decision
States draft → llm_annotated → verified. Teams choose strict (verified only) or open (llm_annotated usable, labeled). LLMs may create and improve unverified definitions; changes to verified definitions always need human approval.

## Consequences
- Every result carries a trust label.
- Guide, don't restrict, by default; lockable for cautious teams.
