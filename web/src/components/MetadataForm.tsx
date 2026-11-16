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
}

/** Step 3: what the definition means and when to use it. */
export function MetadataForm({ value, onChange, errors, showErrors, idTouched, onIdTouched }: Props) {
  const set = <K extends keyof MetadataDraft>(key: K, v: MetadataDraft[K]) => onChange({ ...value, [key]: v });
  const err = (k: keyof MetadataDraft) => (showErrors ? errors[k] : undefined);
  return (
    <div className="meta-form">
      <div className="grid-2">
        <Field label="Title" htmlFor="md-title" required error={err("title")}>
          <input
            id="md-title"
            type="text"
            value={value.title}
            onChange={(e) =>
              onChange({ ...value, title: e.target.value, id: idTouched ? value.id : slugify(e.target.value) })
            }
          />
        </Field>
        <Field label="ID" htmlFor="md-id" required hint="Stable slug agents call it by." error={err("id")}>
          <input
            id="md-id"
            type="text"
            className="mono"
            value={value.id}
            onChange={(e) => {
              onIdTouched();
              set("id", e.target.value);
            }}
          />
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
