import { useState, type FormEvent } from "react";
import type { ProfileUpdate } from "../api/types";
import { profileDraft, profileUpdate } from "../lib/profile";
import { Field } from "./Field";

/** Display name and email (ADR 0029), optional. Sends only what changed; "" clears a field.
 * The server checks the email's format and both lengths. */
export function ProfileForm({
  idPrefix,
  value,
  busy,
  onSave,
  onCancel,
}: {
  idPrefix: string;
  value: { display_name?: string | null; email?: string | null };
  busy: boolean;
  onSave: (body: ProfileUpdate) => void;
  onCancel?: () => void;
}) {
  const current = profileDraft(value);
  const [draft, setDraft] = useState(current);
  const body = profileUpdate(current, draft);

  function submit(e: FormEvent) {
    e.preventDefault();
    if (body) onSave(body);
  }

  return (
    <form className="profile-form" onSubmit={submit} aria-label="Profile" noValidate>
      <div className="grid-2">
        <Field label="Display name" htmlFor={`${idPrefix}-name`} hint="Shown in the menu, user lists and the Log.">
          <input
            id={`${idPrefix}-name`}
            type="text"
            maxLength={80}
            value={draft.display_name}
            onChange={(e) => setDraft({ ...draft, display_name: e.target.value })}
          />
        </Field>
        <Field label="Email" htmlFor={`${idPrefix}-email`} hint="Optional. Visible to you and admins.">
          <input
            id={`${idPrefix}-email`}
            type="email"
            maxLength={254}
            value={draft.email}
            onChange={(e) => setDraft({ ...draft, email: e.target.value })}
          />
        </Field>
      </div>
      <div className="btn-row">
        <button type="submit" className="btn btn-primary btn-sm" disabled={!body || busy}>
          {busy ? "Saving…" : "Save profile"}
        </button>
        {onCancel && (
          <button type="button" className="btn btn-sm" onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>
    </form>
  );
}
