import { useState, type FormEvent } from "react";
import type { RoleCard, RoleInput, ToolInfo, ToolName } from "../api/types";
import { ADMIN_ONLY_TOOLS, ALL_TOOLS, cleanRole, toolSummary } from "../lib/tools";
import { Field } from "./Field";
import { ToolChecklist } from "./ToolChecklist";

interface Props {
  /** null = a new custom role. */
  role: RoleCard | null;
  catalog: ToolInfo[] | null | undefined;
  /** manage_role (and, for the admin role, a human caller). */
  editable: boolean;
  busy: boolean;
  onSave: (name: string, input: RoleInput, isNew: boolean) => void;
  onDelete?: (name: string) => void;
  onCancel?: () => void;
}

const NAME_RULE = /^[a-z][a-z0-9_]*$/;

/**
 * One role (ADR 0027): Allowed (the ceiling for its users' tokens) and On by default (what a
 * new token starts with), grouped use / build / review / admin. The admin role always allows
 * every tool (locked); admin-only tools can be allowed only in the admin role.
 */
export function RoleEditor({ role, catalog, editable, busy, onSave, onDelete, onCancel }: Props) {
  const isNew = role === null;
  const isAdminRole = role?.name === "admin";
  const id = `role-${role?.name ?? "new"}`;
  const [name, setName] = useState(role?.name ?? "");
  const [description, setDescription] = useState(role?.description ?? "");
  const [allowed, setAllowed] = useState<Set<ToolName>>(new Set(isAdminRole ? ALL_TOOLS : (role?.allowed ?? [])));
  const [defaults, setDefaults] = useState<Set<ToolName>>(new Set(role?.defaults ?? []));
  const [reviewAll, setReviewAll] = useState(role?.review_all ?? false);

  function toggle(set: Set<ToolName>, tool: ToolName, on: boolean): Set<ToolName> {
    const next = new Set(set);
    if (on) next.add(tool);
    else next.delete(tool);
    return next;
  }

  function onAllowed(tool: ToolName, on: boolean) {
    setAllowed((prev) => toggle(prev, tool, on));
    if (!on) setDefaults((prev) => toggle(prev, tool, false));
  }

  const nameOk = NAME_RULE.test(name) && !name.includes("__") && name.length <= 32;

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!nameOk) return;
    const cleaned = cleanRole(name, allowed, defaults);
    const input: RoleInput = { description: description.trim(), defaults: cleaned.defaults, review_all: reviewAll };
    if (!isAdminRole) input.allowed = cleaned.allowed;
    else delete input.review_all; // fixed for admin
    onSave(name, input, isNew);
  }

  return (
    <form onSubmit={submit} aria-label={role ? `Role ${role.name}` : "New role"}>
      {isNew ? (
        <Field
          label="Role name"
          htmlFor={`${id}-name`}
          required
          hint="lower_snake_case, e.g. support_bot"
          error={name && !nameOk ? "Use lowercase letters, digits and single underscores." : null}
        >
          <input id={`${id}-name`} type="text" value={name} onChange={(e) => setName(e.target.value)} autoComplete="off" />
        </Field>
      ) : null}
      <Field label="Description" htmlFor={`${id}-description`}>
        <input
          id={`${id}-description`}
          type="text"
          value={description}
          disabled={!editable}
          onChange={(e) => setDescription(e.target.value)}
        />
      </Field>
      <ToolChecklist
        legend={isAdminRole ? "Allowed (always every tool)" : "Allowed"}
        idPrefix={`${id}-allowed`}
        catalog={catalog}
        checked={allowed}
        disabled={!editable}
        enabled={(t) => !isAdminRole && !ADMIN_ONLY_TOOLS.includes(t)}
        onToggle={onAllowed}
      />
      <ToolChecklist
        legend={isAdminRole ? "On by default (admin tools always on)" : "On by default"}
        idPrefix={`${id}-defaults`}
        catalog={catalog}
        checked={defaults}
        disabled={!editable}
        enabled={(t) => allowed.has(t) && !(isAdminRole && ADMIN_ONLY_TOOLS.includes(t))}
        onToggle={(t, on) => setDefaults((prev) => toggle(prev, t, on))}
      />
      <label className="check small">
        <input
          type="checkbox"
          checked={isAdminRole || reviewAll}
          disabled={!editable || isAdminRole}
          onChange={(e) => setReviewAll(e.target.checked)}
        />
        <span>Review everything (otherwise only proposals for definitions the user owns)</span>
      </label>
      {editable && (
        <div className="btn-row mt">
          <button type="submit" className="btn btn-primary btn-sm" disabled={busy || !nameOk}>
            {isNew ? "Create role" : "Save role"}
          </button>
          {onCancel && (
            <button type="button" className="btn btn-sm" onClick={onCancel}>
              Cancel
            </button>
          )}
          {role && onDelete && !role.builtin && (
            <button
              type="button"
              className="btn btn-sm btn-danger-ghost"
              disabled={busy || role.users > 0}
              title={role.users > 0 ? "Give its users another role first" : undefined}
              onClick={() => onDelete(role.name)}
            >
              Delete role
            </button>
          )}
        </div>
      )}
      {role && <p className="muted small mt">Allowed now: {toolSummary(role.allowed)}</p>}
    </form>
  );
}
