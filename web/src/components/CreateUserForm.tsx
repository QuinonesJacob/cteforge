import { useState, type FormEvent } from "react";
import { createUser } from "../api/endpoints";
import type { ActorKind, CreatedUser, RoleCard, UserRole } from "../api/types";
import { useToast } from "../state/ToastContext";
import { Field } from "./Field";

/** Create a user with a role from GET /roles (built-in or custom). A human admin gets the new
 * user's "default" token back once. */
export function CreateUserForm({ roles, onCreated }: { roles: RoleCard[]; onCreated: (created: CreatedUser) => void }) {
  const toast = useToast();
  const [name, setName] = useState("");
  const [role, setRole] = useState<UserRole>("viewer");
  const [kind, setKind] = useState<ActorKind>("human");
  const [busy, setBusy] = useState(false);
  const names = roles.length ? roles.map((r) => r.name) : ["viewer", "contributor", "approver", "admin"];

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    try {
      onCreated(await createUser({ name: name.trim(), role, kind }));
      setName("");
    } catch (err) {
      toast.error(err, "Could not create user");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="create-user" onSubmit={submit} aria-label="Create user">
      <Field label="Name" htmlFor="cu-name" required>
        <input id="cu-name" type="text" value={name} onChange={(e) => setName(e.target.value)} autoComplete="off" />
      </Field>
      <Field label="Role" htmlFor="cu-role">
        <select id="cu-role" value={role} onChange={(e) => setRole(e.target.value)}>
          {names.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
      </Field>
      <fieldset className="radio-group">
        <legend className="field-label">Kind</legend>
        {(["human", "agent"] as const).map((k) => (
          <label key={k} className="check">
            <input type="radio" name="cu-kind" value={k} checked={kind === k} onChange={() => setKind(k)} />
            <span>{k}</span>
          </label>
        ))}
      </fieldset>
      <button type="submit" className="btn btn-primary" disabled={busy || !name.trim()}>
        {busy ? "Creating…" : "Create user"}
      </button>
    </form>
  );
}
