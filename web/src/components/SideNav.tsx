import { useEffect, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { can, canPropose, settingsSections, SETTINGS_LABELS, type SettingsSection } from "../lib/roles";
import { useAuth } from "../state/AuthContext";
import { usePendingCount } from "../state/usePendingCount";
import { Icon, type IconName } from "./Icon";

const SECTION_ICONS: Record<SettingsSection, IconName> = {
  connections: "database",
  team: "shield",
  users: "users",
  roles: "roles",
  tokens: "key",
};

interface Item {
  to: string;
  label: string;
  icon: IconName;
  end?: boolean;
  badge?: number | null;
}

/**
 * The sidebar (ADR 0029): Library · Join map · Import · Log, then Settings and its sections.
 * Links follow the signed-in token's tools (ADR 0027). Below 760px it collapses behind a
 * menu button in the top bar.
 */
export function SideNav() {
  const { user } = useAuth();
  const pending = usePendingCount(can(user, "list_proposals"));
  const [open, setOpen] = useState(false);
  const location = useLocation();

  useEffect(() => setOpen(false), [location.pathname]);

  const main: Item[] = [
    // ADR 0026: one Library (definitions + proposals); its badge counts pending proposals.
    { to: "/", label: "Library", icon: "library", end: true, badge: can(user, "list_proposals") ? pending : null },
    ...(can(user, "list_joins") ? [{ to: "/joins", label: "Join map", icon: "joins" as const }] : []),
    ...(canPropose(user) ? [{ to: "/import", label: "Import", icon: "import" as const }] : []),
    ...(can(user, "view_audit") ? [{ to: "/log", label: "Log", icon: "log" as const }] : []),
  ];
  const settings: Item[] = settingsSections(user).map((s) => ({
    to: `/settings/${s}`,
    label: SETTINGS_LABELS[s],
    icon: SECTION_ICONS[s],
  }));

  const link = (l: Item) => (
    <NavLink key={l.to} to={l.to} end={l.end} className={({ isActive }) => `nav-link ${isActive ? "active" : ""}`}>
      <Icon name={l.icon} />
      <span className="nav-label">{l.label}</span>
      {l.badge != null && l.badge > 0 && (
        <span className="count nav-count" aria-label={`${l.badge} pending`}>
          {l.badge}
        </span>
      )}
    </NavLink>
  );

  return (
    <aside className="sidebar">
      <div className="sidebar-top">
        <NavLink to="/" className="brand" aria-label="cteforge home">
          <span className="brand-mark" aria-hidden="true">
            <Icon name="brand" size={16} strokeWidth={2.4} />
          </span>
          cteforge
        </NavLink>
        <button
          type="button"
          className="icon-btn nav-toggle"
          aria-expanded={open}
          aria-controls="sidebar-navs"
          aria-label={open ? "Close menu" : "Open menu"}
          onClick={() => setOpen((o) => !o)}
        >
          <Icon name={open ? "x" : "menu"} size={20} />
        </button>
      </div>
      <div id="sidebar-navs" className={`sidebar-navs ${open ? "nav-open" : ""}`.trim()}>
        <nav className="nav" aria-label="Primary">
          {main.map(link)}
        </nav>
        {settings.length > 0 && (
          <nav className="nav nav-settings" aria-label="Settings">
            <div className="nav-heading">Settings</div>
            {settings.map(link)}
          </nav>
        )}
      </div>
    </aside>
  );
}
