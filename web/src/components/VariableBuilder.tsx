import type { GrainCandidate, LiteralInfo, RangeCandidate, VariableType } from "../api/types";
import {
  allPairsSuggested,
  descriptionRule,
  orderRangeLiterals,
  rangeShape,
  validateDraft,
  VARIABLE_TYPES,
  type VariableDraft,
} from "../lib/importBuilder";
import { Field } from "./Field";

interface Props {
  draft: VariableDraft;
  onChange: (draft: VariableDraft) => void;
  onSave: () => void;
  onCancel: () => void;
  byId: Record<string, LiteralInfo>;
  ranges: RangeCandidate[];
  grainsById?: Record<string, GrainCandidate>;
  otherNames: string[];
  editing: boolean;
}

/** Name, type, role and description for one variable over the selected literals. */
export function VariableBuilder({ draft, onChange, onSave, onCancel, byId, ranges, grainsById = {}, otherNames, editing }: Props) {
  const rule = descriptionRule(draft, byId, ranges, grainsById);
  const errors = validateDraft(draft, byId, ranges, otherNames, grainsById);
  // A grain variable names date truncations (grain IDs); every other type names literals.
  const isGrain = draft.type === "grain";
  const typeOptions = isGrain ? (["grain"] as VariableType[]) : VARIABLE_TYPES.filter((t) => t !== "grain");
  const shape = draft.type === "date_range" ? rangeShape(draft.literals, ranges) : null;
  const set = <K extends keyof VariableDraft>(key: K, value: VariableDraft[K]) => onChange({ ...draft, [key]: value });

  function setType(type: VariableType) {
    const literals = type === "date_range" ? orderRangeLiterals(draft.literals, ranges, byId) : draft.literals;
    onChange({ ...draft, type, literals });
  }

  const isDate = draft.type === "date" || draft.type === "date_range";
  return (
    <section className="card builder" aria-label={editing ? `Edit variable ${draft.name}` : "New variable"}>
      <h3>{editing ? `Edit variable` : "Make variable"}</h3>
      <p className="small">
        {isGrain ? "Truncations" : "Literals"}:{" "}
        {draft.literals.map((id, i) => (
          <span key={id}>
            {i > 0 && (draft.type === "date_range" && i % 2 === 1 ? " → " : ", ")}
            <code>{id}</code> <span className="muted">({byId[id]?.value ?? grainsById[id]?.unit ?? "?"})</span>
          </span>
        ))}
      </p>
      {draft.type === "date_range" && draft.literals.length % 2 === 0 && (
        <p className="small muted">
          {shape !== null
            ? `Suggested range pair: shape ${shape.replace(/_/g, " ")} is recorded.`
            : allPairsSuggested(draft.literals, ranges)
              ? "Suggested pairs of different shapes: no shape is recorded and the import reports range_not_simple."
              : "Not a suggested pair: shape will be null and the import reports range_not_simple."}
        </p>
      )}
      <div className="grid-2">
        <Field label="Name" htmlFor="vb-name" required hint="lower_snake_case">
          <input id="vb-name" type="text" value={draft.name} onChange={(e) => set("name", e.target.value)} autoComplete="off" />
        </Field>
        <Field label="Type" htmlFor="vb-type" hint="Suggested from the compared type; you decide.">
          <select id="vb-type" value={draft.type} onChange={(e) => setType(e.target.value as VariableType)}>
            {typeOptions.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <div className="role-toggle">
        <label className="check">
          <input
            type="checkbox"
            checked={draft.role === "changeable"}
            onChange={(e) => set("role", e.target.checked ? "changeable" : "fixed")}
          />
          <span>
            <strong>Changeable</strong> — agents may pass a different value.
          </span>
        </label>
        <p className="muted small">
          {draft.role === "fixed"
            ? "Fixed (default): named and visible to agents; overriding it warns and lowers trust."
            : "Changeable: agents choose the value within the input format."}
        </p>
      </div>
      <Field
        label="Description"
        htmlFor="vb-desc"
        required={rule.required}
        hint={
          rule.required ? (
            <>
              Required because: {rule.reasons.join("; ")}. Say what the filter does, since the name alone can't.
            </>
          ) : (
            "What this value controls, in plain language."
          )
        }
      >
        <textarea id="vb-desc" value={draft.description} onChange={(e) => set("description", e.target.value)} rows={2} />
      </Field>
      <div className="grid-2">
        <Field label="Column (hint)" htmlFor="vb-col" hint="What it filters. The type is read from the warehouse.">
          <input id="vb-col" type="text" value={draft.column} onChange={(e) => set("column", e.target.value)} />
        </Field>
        <Field label="Allowed values" htmlFor="vb-allowed" hint="Comma separated. Outside values warn, not block.">
          <input id="vb-allowed" type="text" value={draft.allowed} onChange={(e) => set("allowed", e.target.value)} />
        </Field>
        {draft.type === "number" && (
          <Field label="Unit" htmlFor="vb-unit" hint="What the number means, e.g. TFLOPS, USD.">
            <input id="vb-unit" type="text" value={draft.unit} onChange={(e) => set("unit", e.target.value)} />
          </Field>
        )}
        {isDate && (
          <Field label="Date format" htmlFor="vb-format" hint="strftime pattern for non-ISO date text, e.g. %m-%d-%Y. Empty = ISO.">
            <input id="vb-format" type="text" value={draft.format} onChange={(e) => set("format", e.target.value)} />
          </Field>
        )}
      </div>
      {errors.length > 0 && (
        <ul className="form-errors" aria-live="polite">
          {errors.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      )}
      <div className="btn-row">
        <button type="button" className="btn btn-primary" onClick={onSave} disabled={errors.length > 0}>
          {editing ? "Save variable" : "Add variable"}
        </button>
        <button type="button" className="btn" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </section>
  );
}
