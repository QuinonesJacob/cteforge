import { useState, type FormEvent } from "react";
import type { AuditFilter } from "../api/endpoints";
import { AUDIT_KINDS } from "../api/types";

interface Props {
  value: AuditFilter;
  onApply: (filter: AuditFilter) => void;
}

/** The Log's filters (GET /audit: actor, kind, definition). */
export function LogFilters({ value, onApply }: Props) {
  const [actor, setActor] = useState(value.actor ?? "");
  const [kind, setKind] = useState(value.kind ?? "");
  const [definition, setDefinition] = useState(value.definition_id ?? "");

  function submit(e: FormEvent) {
    e.preventDefault();
    onApply({ actor: actor.trim() || undefined, kind: kind || undefined, definition_id: definition.trim() || undefined });
  }

  function clear() {
    setActor("");
    setKind("");
    setDefinition("");
    onApply({});
  }

  return (
    <form className="toolbar log-filters" onSubmit={submit} aria-label="Filter the log">
      <label className="pill-field">
        <span>Actor</span>
        <input type="text" value={actor} onChange={(e) => setActor(e.target.value)} placeholder="any" />
      </label>
      <label className="pill-field">
        <span>Kind</span>
        <select value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">All calls</option>
          {AUDIT_KINDS.map((k) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
        </select>
      </label>
      <label className="pill-field">
        <span>Definition ID</span>
        <input type="text" className="mono" value={definition} onChange={(e) => setDefinition(e.target.value)} placeholder="any" />
      </label>
      <button type="submit" className="btn btn-primary btn-sm">
        Apply
      </button>
      <button type="button" className="btn btn-sm" onClick={clear}>
        Clear
      </button>
    </form>
  );
}
