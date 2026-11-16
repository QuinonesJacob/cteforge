import { useState, type FormEvent } from "react";
import type { AuditFilter } from "../api/endpoints";
import { AUDIT_KINDS } from "../api/types";
import { Field } from "./Field";

interface Props {
  value: AuditFilter;
  onApply: (filter: AuditFilter) => void;
}

export function AuditFilters({ value, onApply }: Props) {
  const [actor, setActor] = useState(value.actor ?? "");
  const [kind, setKind] = useState(value.kind ?? "");
  const [definition, setDefinition] = useState(value.definition_id ?? "");
  const [limit, setLimit] = useState(value.limit ?? 100);

  function submit(e: FormEvent) {
    e.preventDefault();
    onApply({ actor: actor.trim() || undefined, kind: kind || undefined, definition_id: definition.trim() || undefined, limit });
  }

  function clear() {
    setActor("");
    setKind("");
    setDefinition("");
    onApply({ limit });
  }

  return (
    <form className="card filters" onSubmit={submit} aria-label="Filter audit events">
      <Field label="Actor" htmlFor="af-actor">
        <input id="af-actor" type="text" value={actor} onChange={(e) => setActor(e.target.value)} />
      </Field>
      <Field label="Kind" htmlFor="af-kind">
        <select id="af-kind" value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">All kinds</option>
          {AUDIT_KINDS.map((k) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Definition ID" htmlFor="af-def">
        <input id="af-def" type="text" className="mono" value={definition} onChange={(e) => setDefinition(e.target.value)} />
      </Field>
      <Field label="Limit" htmlFor="af-limit">
        <select id="af-limit" value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
          {[50, 100, 250, 500].map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
      </Field>
      <div className="btn-row filters-buttons">
        <button type="submit" className="btn btn-primary">
          Apply
        </button>
        <button type="button" className="btn" onClick={clear}>
          Clear
        </button>
      </div>
    </form>
  );
}
