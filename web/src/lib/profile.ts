// Display name / email (ADR 0029): form → PUT /me/profile or PATCH /users/{name} body. Pure.
// The server validates (length, email format); this only sends what changed.
import type { ProfileUpdate } from "../api/types";

export interface ProfileDraft {
  display_name: string;
  email: string;
}

export const profileDraft = (u: { display_name?: string | null; email?: string | null }): ProfileDraft => ({
  display_name: u.display_name ?? "",
  email: u.email ?? "",
});

/** Only the changed fields ("" clears one); null when nothing changed. */
export function profileUpdate(current: ProfileDraft, draft: ProfileDraft): ProfileUpdate | null {
  const body: ProfileUpdate = {};
  if (draft.display_name.trim() !== current.display_name.trim()) body.display_name = draft.display_name.trim();
  if (draft.email.trim() !== current.email.trim()) body.email = draft.email.trim();
  return Object.keys(body).length ? body : null;
}
