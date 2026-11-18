import { Fragment, useState } from "react";
import type { CreateTokenRequest, ProfileUpdate, RoleCard, TokenCard, ToolInfo, ToolName, UserView } from "../api/types";
import { formatDateTime, shownName } from "../lib/format";
import { ProfileForm } from "./ProfileForm";
import { toolSummary } from "../lib/tools";
import { TokenForm } from "./TokenForm";
import { TokenList } from "./TokenList";

interface Props {
  users: UserView[];
  roles: RoleCard[];
  catalog: ToolInfo[] | null | undefined;
  me: string | undefined;
  busy: string | null;
  /** manage_user: role, active, revoke. */
  canEdit: boolean;
  /** A human with manage_user: generate tokens for users (agents never see tokens). */
  canMint: boolean;
  onRole: (name: string, role: string) => void;
  onActive: (name: string, active: boolean) => void;
  onRevoke: (user: string, token: TokenCard) => void;
  onGenerate: (user: string, body: CreateTokenRequest) => void;
  /** manage_user: set a user's display name / email (ADR 0029). */
  onProfile?: (user: string, body: ProfileUpdate) => void;
}

/** What a user of `role` may get (agents never verify outside admin; ADR 0027). */
function roleTools(role: RoleCard | undefined, kind: string): ToolName[] {
  if (!role) return [];
  return kind === "agent" && role.name !== "admin" ? role.allowed.filter((t) => t !== "verify") : role.allowed;
}

/** Settings → Users & roles: name (display name, email; ADR 0029), role, active, the role's
 * tools, and each user's tokens (ADR 0027). */
export function UsersTable({ users, roles, catalog, me, busy, canEdit, canMint, onRole, onActive, onRevoke, onGenerate, onProfile }: Props) {
  const [open, setOpen] = useState<string | null>(null);
  const [minting, setMinting] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const roleNames = roles.map((r) => r.name);
  return (
    <div className="table-wrap" tabIndex={0} role="region" aria-label="Users">
      <table>
        <thead>
          <tr>
            <th scope="col">Name</th>
            <th scope="col">Kind</th>
            <th scope="col">Role</th>
            <th scope="col">Tools</th>
            <th scope="col">Active</th>
            <th scope="col">Created</th>
            <th scope="col">Tokens</th>
          </tr>
        </thead>
        <tbody>
          {users.map((u) => {
            const role = roles.find((r) => r.name === u.role);
            const live = (u.tokens ?? []).filter((t) => t.state === "active").length;
            const expanded = open === u.name;
            const names = roleNames.includes(u.role) ? roleNames : [u.role, ...roleNames];
            return (
              <Fragment key={u.name}>
                <tr>
                  <td>
                    <span className="strong">{shownName(u)}</span>
                    {u.name === me && <span className="tag">you</span>}
                    {u.display_name && <div className="mono small muted">{u.name}</div>}
                    {u.email && <div className="small muted">{u.email}</div>}
                  </td>
                  <td>{u.kind}</td>
                  <td>
                    <label className="visually-hidden" htmlFor={`role-${u.name}`}>
                      Role for {u.name}
                    </label>
                    <select
                      id={`role-${u.name}`}
                      className="select-inline"
                      value={u.role}
                      disabled={!canEdit || busy === u.name || u.name === me}
                      title={u.name === me ? "You can't change your own role" : undefined}
                      onChange={(e) => onRole(u.name, e.target.value)}
                    >
                      {names.map((r) => (
                        <option key={r} value={r}>
                          {r}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="small">{role ? toolSummary(roleTools(role, u.kind)) : <span className="muted">—</span>}</td>
                  <td>
                    <label className="check small">
                      <input
                        type="checkbox"
                        checked={u.active}
                        disabled={!canEdit || busy === u.name || u.name === me}
                        aria-label={`${u.name} is active`}
                        onChange={(e) => onActive(u.name, e.target.checked)}
                      />
                      <span>{u.active ? "active" : "deactivated"}</span>
                    </label>
                  </td>
                  <td className="small">{formatDateTime(u.created_at)}</td>
                  <td>
                    <div className="btn-row nowrap">
                    <button
                      type="button"
                      className="btn btn-sm"
                      aria-expanded={expanded}
                      aria-controls={`tokens-${u.name}`}
                      onClick={() => setOpen(expanded ? null : u.name)}
                    >
                      {live} active {expanded ? "▴" : "▾"}
                      <span className="visually-hidden"> tokens of {u.name}</span>
                    </button>
                    {canEdit && onProfile && (
                      <button
                        type="button"
                        className="btn btn-sm"
                        aria-expanded={editing === u.name}
                        onClick={() => setEditing(editing === u.name ? null : u.name)}
                      >
                        Profile<span className="visually-hidden"> of {u.name}</span>
                      </button>
                    )}
                    </div>
                  </td>
                </tr>
                {editing === u.name && onProfile && (
                  <tr>
                    <td colSpan={7}>
                      <ProfileForm
                        idPrefix={`profile-${u.name}`}
                        value={u}
                        busy={busy === u.name}
                        onSave={(b) => {
                          onProfile(u.name, b);
                          setEditing(null);
                        }}
                        onCancel={() => setEditing(null)}
                      />
                    </td>
                  </tr>
                )}
                {expanded && (
                  <tr id={`tokens-${u.name}`}>
                    <td colSpan={7}>
                      <div className="user-tokens stack">
                        <TokenList
                          label={`Tokens of ${u.name}`}
                          owner={u}
                          tokens={u.tokens ?? []}
                          busy={busy}
                          onRevoke={canEdit || u.name === me ? (t) => onRevoke(u.name, t) : undefined}
                        />
                        {canMint && u.active && minting !== u.name && (
                          <div>
                            <button type="button" className="btn btn-sm" onClick={() => setMinting(u.name)}>
                              Generate token for {u.name}
                            </button>
                          </div>
                        )}
                        {canMint && minting === u.name && (
                          <TokenForm
                            idPrefix={`mint-${u.name}`}
                            catalog={catalog}
                            allowed={roleTools(role, u.kind)}
                            defaults={(role?.defaults ?? []).filter((t) => roleTools(role, u.kind).includes(t))}
                            busy={busy === u.name}
                            submitLabel={`Generate token for ${u.name}`}
                            note={
                              <p className="muted small">
                                Generating tokens for other users (admins included) is what manage_user allows: it is
                                equivalent to full admin access, so keep tokens that hold it with people and short-lived.
                              </p>
                            }
                            onSubmit={(b) => {
                              onGenerate(u.name, b);
                              setMinting(null);
                            }}
                            onCancel={() => setMinting(null)}
                          />
                        )}
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
