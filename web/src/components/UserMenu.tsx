import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { initials, shownName } from "../lib/format";
import { toolsOf } from "../lib/roles";
import { useAuth } from "../state/AuthContext";
import { Icon } from "./Icon";
import { ThemeToggle } from "./ThemeToggle";

/** The user menu (ADR 0029): name, email, role, Personal access tokens, Your tools (the
 * token's effective tools), Theme, Sign out. */
export function UserMenu() {
  const { user, authOff, signOut } = useAuth();
  const [open, setOpen] = useState(false);
  const [showTools, setShowTools] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!user) return null;
  const name = shownName(user);
  const tools = toolsOf(user);
  return (
    <div className="user-menu" ref={ref}>
      <button
        type="button"
        className="user-btn"
        aria-haspopup="true"
        aria-expanded={open}
        aria-label={`Your account: ${name}`}
        onClick={() => setOpen((o) => !o)}
      >
        <span className="user-name">{name}</span>
        <span className="avatar" aria-hidden="true">
          {initials(name)}
        </span>
        <Icon name="chevron" size={14} strokeWidth={2.4} />
      </button>
      {open && (
        <div className="menu" role="group" aria-label="Account">
          <div className="menu-head">
            <span className="avatar avatar-lg" aria-hidden="true">
              {initials(name)}
            </span>
            <div className="menu-who">
              <strong>{name}</strong>
              {user.display_name && <span className="muted small mono">{user.name}</span>}
              {user.email && <span className="muted small">{user.email}</span>}
            </div>
            <span className="menu-role">
              {user.role}
              {user.kind === "agent" && " · agent"}
            </span>
          </div>
          <div className="menu-section">
            <Link to="/settings/tokens" className="menu-item" onClick={() => setOpen(false)}>
              <Icon name="key" />
              Personal access tokens
            </Link>
            <button
              type="button"
              className="menu-item"
              aria-expanded={showTools}
              aria-controls="your-tools"
              onClick={() => setShowTools((s) => !s)}
            >
              <Icon name="roles" />
              Your tools
              <span className="menu-meta">{tools.length}</span>
            </button>
            {showTools && (
              <ul id="your-tools" className="menu-tools" aria-label="Your tools">
                {tools.map((t) => (
                  <li key={t} className="mono">
                    {t}
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div className="menu-section menu-row">
            <span>Theme</span>
            <ThemeToggle />
          </div>
          <div className="menu-section">
            {authOff ? (
              <p className="muted small menu-note">Local mode: authentication is off on this server.</p>
            ) : (
              <button
                type="button"
                className="menu-item"
                onClick={() => {
                  setOpen(false);
                  signOut();
                }}
              >
                <Icon name="signout" />
                Sign out
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
