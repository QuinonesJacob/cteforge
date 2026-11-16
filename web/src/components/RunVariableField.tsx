import type { VariableCard } from "../api/types";
import { formatValue } from "../lib/format";
import { outsideAllowed, serializeValue, type FormValue } from "../lib/variableForm";
import { VariableInput } from "./VariableInput";

interface Props {
  card: VariableCard;
  value: FormValue;
  onChange: (value: FormValue) => void;
  overridden: boolean;
  onOverride: (on: boolean) => void;
  error?: string;
}

/** One variable in the run form. Fixed variables are read-only unless explicitly overridden. */
export function RunVariableField({ card, value, onChange, overridden, onOverride, error }: Props) {
  const id = `var-${card.name}`;
  const fixed = card.role === "fixed";
  const locked = fixed && !overridden;
  const serialized = serializeValue(value);
  const notAllowed = serialized.ok && outsideAllowed(serialized.value, card.allowed);
  const hintId = `${id}-hint`;

  return (
    <div className={`run-var ${fixed ? "is-fixed" : ""} ${error ? "field-invalid" : ""}`.trim()}>
      <div className="run-var-head">
        <label htmlFor={id} className="field-label">
          <code>{card.name}</code> <span className="muted small">{card.type}</span>
          {fixed && <span className="tag">fixed</span>}
        </label>
        {fixed && (
          <label className="check small">
            <input type="checkbox" checked={overridden} onChange={(e) => onOverride(e.target.checked)} />
            <span>Override</span>
          </label>
        )}
      </div>
      <VariableInput id={id} value={value} onChange={onChange} disabled={locked} describedBy={hintId} unit={card.unit} />
      <p className="field-hint" id={hintId}>
        {card.description || <span className="muted">No description.</span>}
        <span className="muted"> · format {card.input_format}</span>
        {card.allowed && card.allowed.length > 0 && <span className="muted"> · allowed: {formatValue(card.allowed)}</span>}
      </p>
      {fixed && overridden && (
        <p className="field-warn" role="status">
          Overriding a fixed variable changes trusted logic. The result is labeled one step lower (fixed_override).
        </p>
      )}
      {notAllowed && !locked && (
        <p className="field-warn" role="status">
          Value is outside the allowed list. It will run, with a warning and a lower trust label.
        </p>
      )}
      {error && (
        <p className="field-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
