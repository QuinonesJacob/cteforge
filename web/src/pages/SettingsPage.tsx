import { useState } from "react";
import {
  createToken,
  getSettings,
  listProfiles,
  listRoles,
  listTools,
  listUsers,
  putSettings,
  revokeToken,
  updateUser,
} from "../api/endpoints";
import type { CreatedUser, CreateTokenRequest, TeamMode, TokenCard, ToolInfo, User } from "../api/types";
import { CreateUserForm } from "../components/CreateUserForm";
import { DefaultConnectionCard } from "../components/DefaultConnectionCard";
import { ErrorState } from "../components/ErrorState";
import { Loading } from "../components/Loading";
import { MyTokensCard } from "../components/MyTokensCard";
import { PageHeader } from "../components/PageHeader";
import { RolesCard } from "../components/RolesCard";
import { TeamModeCard } from "../components/TeamModeCard";
import { TokenReveal } from "../components/TokenReveal";
import { UsersTable } from "../components/UsersTable";
import { canManageRoles, canManageSettings, canManageUsers, canSeeUsers } from "../lib/roles";
import { useAuth } from "../state/AuthContext";
import { useToast } from "../state/ToastContext";
import { useAsync } from "../state/useAsync";

/**
 * Settings (ADR 0027): My tokens for everyone; team mode and default connection with
 * manage_settings; Users and Roles with list_users (editing with manage_user / manage_role).
 * Each admin section is mounted only when the signed-in token holds its tool, so others never
 * call those routes. The server enforces every rule again.
 */
export function SettingsPage() {
  const { user } = useAuth();
  const catalog = useAsync((signal) => listTools({ signal }), []);
  if (!user) return null;
  const tools = catalog.error ? null : catalog.data;
  return (
    <div className="page">
      <PageHeader title="Settings" subtitle="Your tokens, and — for admins — the team's settings, users and roles." />
      <MyTokensCard user={user} catalog={tools} />
      {canManageSettings(user) && <TeamSettingsSections />}
      {canSeeUsers(user) && <AccessSections user={user} catalog={tools} />}
    </div>
  );
}

function TeamSettingsSections() {
  const toast = useToast();
  const settings = useAsync((signal) => getSettings({ signal }), []);
  const profiles = useAsync((signal) => listProfiles({ signal }), []);
  const [savingMode, setSavingMode] = useState(false);
  const [savingConnection, setSavingConnection] = useState(false);

  async function changeMode(mode: TeamMode) {
    setSavingMode(true);
    try {
      const s = await putSettings({ mode });
      settings.setData(s?.mode ? s : { ...settings.data, mode });
      toast.success(`Team mode set to ${mode}`);
    } catch (err) {
      toast.error(err, "Could not change team mode");
    } finally {
      setSavingMode(false);
    }
  }

  async function changeConnection(name: string | null) {
    setSavingConnection(true);
    try {
      const s = await putSettings({ default_connection: name });
      if (s) settings.setData(s);
      profiles.reload(); // `default: true` moves with it
      toast.success(name ? `Default connection set to ${name}` : "Default connection cleared");
    } catch (err) {
      toast.error(err, "Could not change the default connection");
    } finally {
      setSavingConnection(false);
    }
  }

  return (
    <>
      <section className="card">
        <h2>Team mode</h2>
        {settings.loading && !settings.data ? (
          <Loading />
        ) : settings.error ? (
          <ErrorState error={settings.error} onRetry={settings.reload} />
        ) : (
          <TeamModeCard mode={settings.data?.mode ?? "strict"} busy={savingMode} onChange={(m) => void changeMode(m)} />
        )}
      </section>
      <section className="card">
        <h2>Default connection</h2>
        {settings.loading && !settings.data ? (
          <Loading />
        ) : settings.error ? (
          <ErrorState error={settings.error} onRetry={settings.reload} />
        ) : settings.data ? (
          <DefaultConnectionCard
            settings={settings.data}
            profiles={profiles.error ? [] : (profiles.data ?? null)}
            busy={savingConnection}
            onChange={(n) => void changeConnection(n)}
          />
        ) : null}
      </section>
    </>
  );
}

/** Users and roles (list_users); controls only with manage_user / manage_role. */
function AccessSections({ user, catalog }: { user: User; catalog: ToolInfo[] | null | undefined }) {
  const toast = useToast();
  const users = useAsync((signal) => listUsers({ signal }), []);
  const roles = useAsync((signal) => listRoles({ signal }), []);
  const [busyUser, setBusyUser] = useState<string | null>(null);
  const [revealed, setRevealed] = useState<{ name: string; token: string; tokenName?: string } | null>(null);
  const manageUsers = canManageUsers(user);
  const mint = manageUsers && user.kind === "human";

  async function changeUser(name: string, change: { role?: string; active?: boolean }, done: string) {
    setBusyUser(name);
    try {
      const updated = await updateUser(name, change);
      users.setData((users.data ?? []).map((u) => (u.name === name ? updated : u)));
      roles.reload(); // user counts
      toast.success(done);
    } catch (err) {
      toast.error(err, "Could not change the user");
    } finally {
      setBusyUser(null);
    }
  }

  async function revoke(name: string, token: TokenCard) {
    if (!window.confirm(`Revoke the token "${token.name}" of ${name}? It stops working on its next call.`)) return;
    setBusyUser(token.id);
    try {
      await revokeToken(token.id, name === user.name ? undefined : name);
      toast.success(`Token "${token.name}" of ${name} revoked`);
      users.reload();
    } catch (err) {
      toast.error(err, "Could not revoke the token");
    } finally {
      setBusyUser(null);
    }
  }

  async function generate(name: string, body: CreateTokenRequest) {
    setBusyUser(name);
    try {
      const made = await createToken(body, name === user.name ? undefined : name);
      setRevealed({ name, token: made.token, tokenName: made.info.name });
      users.reload();
    } catch (err) {
      toast.error(err, "Could not generate the token");
    } finally {
      setBusyUser(null);
    }
  }

  function onCreated(created: CreatedUser) {
    if (created.token) setRevealed({ name: created.user.name, token: created.token, tokenName: "default" });
    users.reload();
    roles.reload();
  }

  return (
    <>
      <section className="card">
        <h2>Users</h2>
        {revealed && (
          <TokenReveal
            name={revealed.name}
            tokenName={revealed.tokenName}
            token={revealed.token}
            onDismiss={() => setRevealed(null)}
          />
        )}
        {manageUsers && (
          <>
            <h3 className="mt">Create user</h3>
            <CreateUserForm roles={roles.data ?? []} onCreated={onCreated} />
          </>
        )}
        <h3 className="mt">All users</h3>
        {users.loading && !users.data ? (
          <Loading />
        ) : users.error ? (
          <ErrorState error={users.error} onRetry={users.reload} />
        ) : (
          <UsersTable
            users={users.data ?? []}
            roles={roles.data ?? []}
            catalog={catalog}
            me={user.name}
            busy={busyUser}
            canEdit={manageUsers}
            canMint={mint}
            onRole={(n, r) => void changeUser(n, { role: r }, `${n} is now ${r}`)}
            onActive={(n, a) => void changeUser(n, { active: a }, a ? `${n} is active` : `${n} is deactivated`)}
            onRevoke={(n, t) => void revoke(n, t)}
            onGenerate={(n, b) => void generate(n, b)}
          />
        )}
      </section>
      <section className="card">
        <h2>Roles</h2>
        <p className="muted small">
          A role's Allowed tools are the most any of its users' tokens can have; On by default is what a new token starts
          with. The admin role always allows every tool.
        </p>
        {roles.loading && !roles.data ? (
          <Loading />
        ) : roles.error ? (
          <ErrorState error={roles.error} onRetry={roles.reload} />
        ) : (
          <RolesCard
            roles={roles.data ?? []}
            catalog={catalog}
            canEdit={canManageRoles(user)}
            humanCaller={user.kind === "human"}
            onChanged={roles.reload}
          />
        )}
      </section>
    </>
  );
}
