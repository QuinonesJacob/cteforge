import { useEffect, useRef, useState } from "react";
import { useAuth } from "../state/AuthContext";

export function UserMenu() {
  const { user, authOff, signOut } = useAuth();
  const [open, setOpen] = useState(false);
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
  return (
    <div className="user-menu" ref={ref}>
      <button
        type="button"
        className="user-btn"
        aria-haspopup="true"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <span className="avatar" aria-hidden="true">
          {user.name.slice(0, 1).toUpperCase()}
        </span>
        <span className="user-name">{user.name}</span>
        <span className="tag user-role">{user.role}</span>
      </button>
      {open && (
        <div className="menu" role="group" aria-label="Account">
          <p className="menu-head">
            <strong>{user.name}</strong>
            <span className="muted small">
              {user.role} · {user.kind}
            </span>
          </p>
          {authOff ? (
            <p className="muted small menu-note">Local mode: authentication is off on this server.</p>
          ) : (
            <button
              type="button"
              className="btn btn-sm btn-block"
              onClick={() => {
                setOpen(false);
                signOut();
              }}
            >
              Sign out
            </button>
          )}
        </div>
      )}
    </div>
  );
}
