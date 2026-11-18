import { useState } from "react";
import { createRole, deleteRole, updateRole } from "../api/endpoints";
import type { RoleCard, RoleInput, ToolInfo } from "../api/types";
import { useToast } from "../state/ToastContext";
import { RoleEditor } from "./RoleEditor";

interface Props {
  roles: RoleCard[];
  catalog: ToolInfo[] | null | undefined;
  /** manage_role. */
  canEdit: boolean;
  /** Only a person edits the admin role (ADR 0027). */
  humanCaller: boolean;
  onChanged: () => void;
}

/** Settings → Roles (ADR 0027): each role's Allowed / On-by-default tools; custom roles. */
export function RolesCard({ roles, catalog, canEdit, humanCaller, onChanged }: Props) {
  const toast = useToast();
  const [open, setOpen] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);

  async function save(name: string, input: RoleInput, isNew: boolean) {
    setBusy(true);
    try {
      if (isNew) await createRole({ name, ...input });
      else await updateRole(name, input);
      toast.success(isNew ? `Role ${name} created` : `Role ${name} saved`);
      setCreating(false);
      setOpen(null);
      onChanged();
    } catch (err) {
      toast.error(err, isNew ? "Could not create the role" : "Could not save the role");
    } finally {
      setBusy(false);
    }
  }

  async function remove(name: string) {
    if (!window.confirm(`Delete the role ${name}?`)) return;
    setBusy(true);
    try {
      await deleteRole(name);
      toast.success(`Role ${name} deleted`);
      setOpen(null);
      onChanged();
    } catch (err) {
      toast.error(err, "Could not delete the role");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <ul className="role-list">
        {roles.map((r) => {
          const expanded = open === r.name;
          const editable = canEdit && (r.name !== "admin" || humanCaller);
          return (
            <li key={r.name} className="role-item">
              <div className="role-item-head">
                <h3>
                  {r.name} {r.builtin && <span className="tag">built-in</span>}
                </h3>
                <span className="muted small">
                  {r.users} {r.users === 1 ? "user" : "users"} · {r.allowed.length} allowed · {r.defaults.length} on by
                  default
                </span>
                <button
                  type="button"
                  className="btn btn-sm"
                  aria-expanded={expanded}
                  onClick={() => setOpen(expanded ? null : r.name)}
                >
                  {expanded ? "Close" : editable ? "Edit" : "View"}
                  <span className="visually-hidden"> role {r.name}</span>
                </button>
              </div>
              {r.description && <p className="muted small">{r.description}</p>}
              {expanded && (
                <RoleEditor
                  key={`${r.name}-${r.allowed.join()}-${r.defaults.join()}`}
                  role={r}
                  catalog={catalog}
                  editable={editable}
                  busy={busy}
                  onSave={(n, input, isNew) => void save(n, input, isNew)}
                  onDelete={(n) => void remove(n)}
                />
              )}
            </li>
          );
        })}
      </ul>
      {canEdit && !creating && (
        <button type="button" className="btn btn-sm mt" onClick={() => setCreating(true)}>
          New role
        </button>
      )}
      {canEdit && creating && (
        <div className="role-item mt">
          <h3>New role</h3>
          <RoleEditor
            role={null}
            catalog={catalog}
            editable
            busy={busy}
            onSave={(n, input, isNew) => void save(n, input, isNew)}
            onCancel={() => setCreating(false)}
          />
        </div>
      )}
    </>
  );
}
