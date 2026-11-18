import { useState } from "react";
import type { MetadataDraft } from "../lib/importBuilder";
import { slugify } from "../lib/importBuilder";
import { Field } from "./Field";

interface Props {
  value: MetadataDraft;
  onChange: (value: MetadataDraft) => void;
  errors: Partial<Record<keyof MetadataDraft, string>>;
  showErrors: boolean;
  idTouched: boolean;
  onIdTouched: () => void;
  /** Revising: the ID can't be edited here; renames go through "Rename ID" (ADR 0024). */
  idLocked?: boolean;
}

/**
 * Step 3: what the definition means and when to use it. A new definition's ID is filled in
 * from the title and locked; "Edit ID" unlocks it (ADR 0024).
 */
export function MetadataForm({ value, onChange, errors, showErrors, idTouched, onIdTouched, idLocked = false }: Props) {
  // A remount (Back, then Continue) keeps an ID the person typed editable.
  const [idEditing, setIdEditing] = useState(() => idTouched && !idLocked);
  const set = <K extends keyof MetadataDraft>(key: K, v: MetadataDraft[K]) => onChange({ ...value, [key]: v });
  const err = (k: keyof MetadataDraft) => (showErrors ? errors[k] : undefined);
  const idReadOnly = idLocked || !idEditing;
  const idHint = idLocked
    ? "Agents and composed definitions use this name. To change it, use Rename ID on the definition page."
    : idEditing
      ? "Agents and composed definitions use this name. lower_snake_case: letters, digits, single underscores."
      : "Filled in from the title. Agents and composed definitions use this name.";
  return (
    <div className="meta-form">
      <div className="grid-2">
        <Field label="Title" htmlFor="md-title" required error={err("title")}>
          <input
            id="md-title"
            type="text"
            value={value.title}
            onChange={(e) =>
              onChange({ ...value, title: e.target.value, id: idReadOnly && !idLocked ? slugify(e.target.value) : value.id })
            }
          />
        </Field>
        <Field
          label="ID"
          htmlFor="md-id"
          required
          hint={idHint}
          error={err("id")}
        >
          <div className="id-field">
            <input
              id="md-id"
              type="text"
              className="mono"
              readOnly={idReadOnly}
              aria-describedby="md-id-hint"
              value={value.id}
              onChange={(e) => {
                onIdTouched();
                set("id", e.target.value);
              }}
            />
            {!idLocked &&
              (idEditing ? (
                <button
                  type="button"
                  className="btn btn-sm"
                  onClick={() => {
                    setIdEditing(false);
                    onChange({ ...value, id: slugify(value.title) });
                  }}
                >
                  Use title
                </button>
              ) : (
                <button type="button" className="btn btn-sm" onClick={() => setIdEditing(true)}>
                  Edit ID
                </button>
              ))}
          </div>
        </Field>
      </div>
      <Field label="Meaning" htmlFor="md-meaning" required hint="What the result means, in plain language." error={err("meaning")}>
        <textarea id="md-meaning" rows={3} value={value.meaning} onChange={(e) => set("meaning", e.target.value)} />
      </Field>
      <Field label="Keywords" htmlFor="md-keywords" hint="Comma separated synonyms people search for.">
        <input id="md-keywords" type="text" value={value.keywords} onChange={(e) => set("keywords", e.target.value)} />
      </Field>
      <div className="grid-2">
        <Field label="Use when" htmlFor="md-use">
          <textarea id="md-use" rows={2} value={value.use_when} onChange={(e) => set("use_when", e.target.value)} />
        </Field>
        <Field label="Don't use when" htmlFor="md-dont">
          <textarea id="md-dont" rows={2} value={value.dont_use_when} onChange={(e) => set("dont_use_when", e.target.value)} />
        </Field>
      </div>
      <div className="grid-3">
        <Field label="Grain" htmlFor="md-grain" hint="e.g. one row per account per month">
          <input id="md-grain" type="text" value={value.grain} onChange={(e) => set("grain", e.target.value)} />
        </Field>
        <Field label="Join keys" htmlFor="md-joinkeys" hint="Output columns safe to join on, comma separated.">
          <input id="md-joinkeys" type="text" className="mono" value={value.join_keys} onChange={(e) => set("join_keys", e.target.value)} />
        </Field>
        <Field label="Owner" htmlFor="md-owner" hint="User who may approve changes.">
          <input id="md-owner" type="text" value={value.owner} onChange={(e) => set("owner", e.target.value)} />
        </Field>
      </div>
      <Field label="Proposal summary" htmlFor="md-summary" hint="One line for reviewers.">
        <input id="md-summary" type="text" value={value.summary} onChange={(e) => set("summary", e.target.value)} />
      </Field>
    </div>
  );
}
