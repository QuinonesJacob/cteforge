import type { User, UserRole } from "../api/types";
import { formatDateTime } from "../lib/format";
import { ROLES } from "../lib/roles";

interface Props {
  users: User[];
  me: string | undefined;
  busy: string | null;
  onRole: (name: string, role: UserRole) => void;
  onRotate: (name: string) => void;
}

export function UsersTable({ users, me, busy, onRole, onRotate }: Props) {
  return (
    <div className="table-wrap" tabIndex={0} role="region" aria-label="Users">
      <table>
        <thead>
          <tr>
            <th scope="col">Name</th>
            <th scope="col">Kind</th>
            <th scope="col">Role</th>
            <th scope="col">Created</th>
            <th scope="col">
              <span className="visually-hidden">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {users.map((u) => (
            <tr key={u.name}>
              <td>
                {u.name}
                {u.name === me && <span className="tag">you</span>}
              </td>
              <td>{u.kind}</td>
              <td>
                <label className="visually-hidden" htmlFor={`role-${u.name}`}>
                  Role for {u.name}
                </label>
                <select
                  id={`role-${u.name}`}
                  value={u.role}
                  disabled={busy === u.name || u.name === me}
                  title={u.name === me ? "You can't change your own role" : undefined}
                  onChange={(e) => onRole(u.name, e.target.value as UserRole)}
                >
                  {ROLES.map((r) => (
                    <option key={r} value={r}>
                      {r}
                    </option>
                  ))}
                </select>
              </td>
              <td className="small">{formatDateTime(u.created_at)}</td>
              <td>
                <button type="button" className="btn btn-sm" disabled={busy === u.name} onClick={() => onRotate(u.name)}>
                  Rotate token
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
