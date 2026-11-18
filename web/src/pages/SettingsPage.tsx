import { useState } from "react";
import { getSettings, listProfiles, listUsers, putSettings, rotateToken, setUserRole } from "../api/endpoints";
import type { CreatedUser, TeamMode, User, UserRole } from "../api/types";
import { CreateUserForm } from "../components/CreateUserForm";
import { DefaultConnectionCard } from "../components/DefaultConnectionCard";
import { ErrorState } from "../components/ErrorState";
import { Loading } from "../components/Loading";
import { PageHeader } from "../components/PageHeader";
import { TeamModeCard } from "../components/TeamModeCard";
import { TokenReveal } from "../components/TokenReveal";
import { UsersTable } from "../components/UsersTable";
import { isAdmin } from "../lib/roles";
import { useAuth } from "../state/AuthContext";
import { useToast } from "../state/ToastContext";
import { useAsync } from "../state/useAsync";

/** Settings (admins only): team mode, default connection, users. */
export function SettingsPage() {
  const { user } = useAuth();
  if (!isAdmin(user) || !user)
    return (
      <div className="page">
        <PageHeader title="Settings" />
        <p className="callout callout-info">Settings and users are for admins only.</p>
      </div>
    );
  return (
    <div className="page">
      <PageHeader title="Settings" subtitle="Team mode, default connection and who can do what." />
      <TeamAdmin user={user} />
    </div>
  );
}

/** Admin-only sections; mounted only for admins so others never call admin routes. */
function TeamAdmin({ user }: { user: User }) {
  const toast = useToast();
  const settings = useAsync((signal) => getSettings({ signal }), []);
  const users = useAsync((signal) => listUsers({ signal }), []);
  const profiles = useAsync((signal) => listProfiles({ signal }), []);
  const [savingMode, setSavingMode] = useState(false);
  const [savingConnection, setSavingConnection] = useState(false);
  const [busyUser, setBusyUser] = useState<string | null>(null);
  const [revealed, setRevealed] = useState<CreatedUser | null>(null);

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

  async function changeRole(name: string, role: UserRole) {
    setBusyUser(name);
    try {
      await setUserRole(name, role);
      users.setData((users.data ?? []).map((u) => (u.name === name ? { ...u, role } : u)));
      toast.success(`${name} is now ${role}`);
    } catch (err) {
      toast.error(err, "Could not change role");
    } finally {
      setBusyUser(null);
    }
  }

  async function rotate(name: string) {
    if (!window.confirm(`Rotate the token for ${name}? Their current token stops working immediately.`)) return;
    setBusyUser(name);
    try {
      const created = await rotateToken(name);
      const existing = (users.data ?? []).find((u) => u.name === name);
      setRevealed({ token: created.token, user: existing ?? created.user });
    } catch (err) {
      toast.error(err, "Could not rotate token");
    } finally {
      setBusyUser(null);
    }
  }

  function onCreated(created: CreatedUser) {
    setRevealed(created);
    users.reload();
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
      <section className="card">
        <h2>Users</h2>
        {revealed && (
          <TokenReveal name={revealed.user.name} token={revealed.token} onDismiss={() => setRevealed(null)} />
        )}
        <h3 className="mt">Create user</h3>
        <CreateUserForm onCreated={onCreated} />
        <h3 className="mt">All users</h3>
        {users.loading && !users.data ? (
          <Loading />
        ) : users.error ? (
          <ErrorState error={users.error} onRetry={users.reload} />
        ) : (
          <UsersTable
            users={users.data ?? []}
            me={user.name}
            busy={busyUser}
            onRole={(n, r) => void changeRole(n, r)}
            onRotate={(n) => void rotate(n)}
          />
        )}
      </section>
    </>
  );
}
