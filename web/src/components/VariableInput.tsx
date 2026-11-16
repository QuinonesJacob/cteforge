import type { FormValue } from "../lib/variableForm";
import { GRAINS } from "../lib/variableForm";
import { ChipsInput } from "./ChipsInput";

interface Props {
  id: string;
  value: FormValue;
  onChange: (value: FormValue) => void;
  disabled?: boolean;
  describedBy?: string;
  unit?: string | null;
}

/** One input control per variable type (DESIGN §4). */
export function VariableInput({ id, value, onChange, disabled, describedBy, unit }: Props) {
  switch (value.kind) {
    case "date_range":
      return (
        <div className="range-inputs" id={id} role="group" aria-describedby={describedBy}>
          <label className="inline-label">
            <span>Start</span>
            <input
              type="date"
              value={value.start}
              disabled={disabled}
              onChange={(e) => onChange({ ...value, start: e.target.value })}
            />
          </label>
          <label className="inline-label">
            <span>End</span>
            <input
              type="date"
              value={value.end}
              disabled={disabled}
              onChange={(e) => onChange({ ...value, end: e.target.value })}
            />
          </label>
        </div>
      );
    case "date":
      return (
        <input
          id={id}
          type="date"
          value={value.value}
          disabled={disabled}
          aria-describedby={describedBy}
          onChange={(e) => onChange({ ...value, value: e.target.value })}
        />
      );
    case "text_list":
      return (
        <ChipsInput
          id={id}
          values={value.items}
          disabled={disabled}
          describedBy={describedBy}
          onChange={(items) => onChange({ ...value, items })}
        />
      );
    case "text":
      return (
        <input
          id={id}
          type="text"
          value={value.value}
          disabled={disabled}
          aria-describedby={describedBy}
          onChange={(e) => onChange({ ...value, value: e.target.value })}
        />
      );
    case "number":
      return (
        <div className="with-unit">
          <input
            id={id}
            type="text"
            inputMode="decimal"
            value={value.value}
            disabled={disabled}
            aria-describedby={describedBy}
            onChange={(e) => onChange({ ...value, value: e.target.value })}
          />
          {unit && <span className="unit">{unit}</span>}
        </div>
      );
    case "boolean":
      return (
        <label className="check">
          <input
            id={id}
            type="checkbox"
            checked={value.value}
            disabled={disabled}
            aria-describedby={describedBy}
            onChange={(e) => onChange({ ...value, value: e.target.checked })}
          />
          <span>{value.value ? "true" : "false"}</span>
        </label>
      );
    case "grain":
      return (
        <select
          id={id}
          value={value.value}
          disabled={disabled}
          aria-describedby={describedBy}
          onChange={(e) => onChange({ ...value, value: e.target.value })}
        >
          {GRAINS.map((g) => (
            <option key={g} value={g}>
              {g}
            </option>
          ))}
        </select>
      );
  }
}
