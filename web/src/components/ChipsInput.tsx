import { useState, type KeyboardEvent } from "react";

interface Props {
  id: string;
  values: string[];
  onChange: (values: string[]) => void;
  placeholder?: string;
  disabled?: boolean;
  describedBy?: string;
}

/** List of text values as removable chips. Enter or comma adds; Backspace on empty removes. */
export function ChipsInput({ id, values, onChange, placeholder = "Type and press Enter", disabled, describedBy }: Props) {
  const [draft, setDraft] = useState("");

  function add(raw: string) {
    const items = raw
      .split(",")
      .map((s) => s.trim())
      .filter((s) => s && !values.includes(s));
    if (items.length) onChange([...values, ...items]);
    setDraft("");
  }

  function onKey(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      add(draft);
    } else if (e.key === "Backspace" && draft === "" && values.length) {
      onChange(values.slice(0, -1));
    }
  }

  return (
    <div className={`chips-input ${disabled ? "is-disabled" : ""}`.trim()}>
      <ul className="chips" aria-label="Values">
        {values.map((v) => (
          <li key={v} className="chip">
            <span>{v}</span>
            {!disabled && (
              <button
                type="button"
                className="chip-x"
                aria-label={`Remove ${v}`}
                onClick={() => onChange(values.filter((x) => x !== v))}
              >
                ×
              </button>
            )}
          </li>
        ))}
      </ul>
      {!disabled && (
        <input
          id={id}
          type="text"
          value={draft}
          placeholder={values.length ? "" : placeholder}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKey}
          onBlur={() => draft.trim() && add(draft)}
          aria-describedby={describedBy}
        />
      )}
    </div>
  );
}
