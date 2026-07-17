import { Bot, History, PlayCircle, Settings } from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";

import "./styles.css";

const navigation = [
  { label: "New review", to: "/", icon: PlayCircle, end: true },
  { label: "Runs", to: "/runs", icon: History, end: false },
  { label: "Review agents", to: "/agents", icon: Bot, end: false },
  { label: "Settings", to: "/settings", icon: Settings, end: false },
] as const;

export function App() {
  return (
    <div className="app-shell">
      <header className="topbar">
        <span className="brand-sigil" aria-hidden="true">
          CL
        </span>
        <strong>CodeLens</strong>
        <span className="topbar-context">Local review workbench</span>
      </header>

      <aside className="sidebar" aria-label="Primary navigation">
        <p className="sidebar-label">Workspace</p>
        <nav>
          {navigation.map(({ end, icon: Icon, label, to }) => (
            <NavLink
              className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}
              end={end}
              key={to}
              to={to}
            >
              <Icon aria-hidden="true" />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-boundary">
          <span className="boundary-dot" aria-hidden="true" />
          Loopback only
        </div>
      </aside>

      <main className="main-content">
        <Outlet />
      </main>
    </div>
  );
}

