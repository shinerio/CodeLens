import { Blocks, Bot, History, Settings } from "lucide-react";
import { NavLink, Outlet, useLocation } from "react-router-dom";

import { useI18n } from "../shared/i18n/i18n";
import "./styles.css";

export function App() {
  const { locale, setLocale, t } = useI18n();
  const location = useLocation();
  const isNewReview = location.pathname === "/reviews/new";

  return (
    <div className="app-shell">
      <aside className="sidebar" aria-label={t("nav.primary")}>
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">CL</span>
          <span className="brand-copy"><strong>CodeLens</strong><small>{t("app.context")}</small></span>
        </div>
        <nav>
          <section className="nav-section">
            <p className="nav-label">{t("nav.workspace")}</p>
            <NavLink className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")} to="/runs">
              <History aria-hidden="true" />
              <span>{t("nav.runs")}</span>
            </NavLink>
          </section>

          <div className="nav-section navigation-secondary">
            <p className="nav-label">{t("nav.configuration")}</p>
            <NavLink className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")} to="/agents">
              <Bot aria-hidden="true" />
              <span>{t("nav.reviewAgents")}</span>
            </NavLink>
            <NavLink className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")} to="/capabilities">
              <Blocks aria-hidden="true" />
              <span>{t("nav.capabilities")}</span>
            </NavLink>
            <NavLink
              className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}
              to="/settings"
            >
              <Settings aria-hidden="true" />
              <span>{t("nav.settings")}</span>
            </NavLink>
          </div>
        </nav>
        <div className="sidebar-footer"><div className="sidebar-boundary">
          <span className="boundary-dot state-dot" aria-hidden="true" />
          {t("nav.loopback")}
        </div></div>
      </aside>

      <div className="workspace">
        <header className="topbar">
          <div className="breadcrumb"><span>{t("nav.runs")}</span><span aria-hidden="true">/</span><strong>{isNewReview ? t("nav.newReview") : t("nav.workspace")}</strong></div>
          <div className="topbar-actions">
            <span className="topbar-context">{t("app.context")}</span>
            <button
              aria-label={t("app.languageSwitch")}
              className="language-switch"
              type="button"
              onClick={() => setLocale(locale === "en" ? "zh-CN" : "en")}
            >
              {t("app.languageSwitch")}
            </button>
          </div>
        </header>
        <main className="main-content"><Outlet /></main>
      </div>
    </div>
  );
}
