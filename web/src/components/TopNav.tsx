import { useEffect, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { isAdmin } from "../lib/roles";
import { useAuth } from "../state/AuthContext";
import { usePendingCount } from "../state/usePendingCount";
import { ThemeToggle } from "./ThemeToggle";
import { UserMenu } from "./UserMenu";

export function TopNav() {
  const { user } = useAuth();
  const pending = usePendingCount(Boolean(user));
  const [open, setOpen] = useState(false);
  const location = useLocation();

  useEffect(() => setOpen(false), [location.pathname]);

  const links = [
    { to: "/", label: "Library", end: true },
    { to: "/import", label: "Import" },
    { to: "/review", label: "Review", badge: pending },
    { to: "/audit", label: "Audit" },
    ...(isAdmin(user) ? [{ to: "/settings", label: "Settings" }] : []),
  ];

  return (
    <header className="topnav">
      <div className="topnav-inner">
        <NavLink to="/" className="brand" aria-label="cteforge home">
          <span className="brand-mark" aria-hidden="true">
            c
          </span>
          cteforge
        </NavLink>
        <button
          type="button"
          className="icon-btn nav-toggle"
          aria-expanded={open}
          aria-controls="primary-nav"
          aria-label={open ? "Close menu" : "Open menu"}
          onClick={() => setOpen((o) => !o)}
        >
          <span aria-hidden="true">{open ? "×" : "☰"}</span>
        </button>
        <nav id="primary-nav" className={`nav ${open ? "nav-open" : ""}`.trim()} aria-label="Primary">
          {links.map((l) => (
            <NavLink key={l.to} to={l.to} end={l.end} className={({ isActive }) => `nav-link ${isActive ? "active" : ""}`}>
              {l.label}
              {l.badge != null && l.badge > 0 && (
                <span className="count" aria-label={`${l.badge} pending`}>
                  {l.badge}
                </span>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="topnav-right">
          <ThemeToggle />
          <UserMenu />
        </div>
      </div>
    </header>
  );
}
