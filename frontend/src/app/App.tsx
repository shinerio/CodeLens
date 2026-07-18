import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Blocks, Bot, FolderKanban, History, Plus, Settings, Trash2 } from "lucide-react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";

import { deleteReview, listReviews } from "../features/reviews/api";
import { useI18n } from "../shared/i18n/i18n";
import "./styles.css";

export function App() {
  const { locale, t } = useI18n();
  const queryClient = useQueryClient();
  const location = useLocation();
  const navigate = useNavigate();
  const reviewsQuery = useQuery({ queryKey: ["reviews"], queryFn: listReviews });
  const deleteMutation = useMutation({
    mutationFn: deleteReview,
    onSuccess: async (_, taskId) => {
      if (location.pathname === `/reviews/${taskId}`) {
        navigate("/reviews/new");
      }
      await queryClient.invalidateQueries({ queryKey: ["reviews"] });
    },
  });

  function handleDelete(taskId: string, repositoryName: string) {
    if (window.confirm(t("nav.deleteConfirm", { name: repositoryName }))) {
      deleteMutation.mutate(taskId);
    }
  }

  const dateFormatter = new Intl.DateTimeFormat(locale, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
  const isNewReview = location.pathname === "/reviews/new";

  return (
    <div className="app-shell">
      <aside className="sidebar" aria-label={t("nav.primary")}>
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">CL</span>
          <span className="brand-copy"><strong>CodeLens</strong><small>{t("app.context")}</small></span>
        </div>
        <nav>
          <section className="nav-section review-navigation">
            <p className="nav-label">{t("nav.workspace")}</p>
            <div className="review-navigation__heading">
              <span><FolderKanban aria-hidden="true" /> {t("nav.reviews")}</span>
              <button
                aria-label={t("nav.newReview")}
                className="review-navigation__create"
                type="button"
                onClick={() => navigate("/reviews/new")}
              >
                <Plus aria-hidden="true" />
              </button>
            </div>
            <div className="review-workspace-list">
              {reviewsQuery.isLoading ? (
                <p className="review-workspace-empty">{t("common.loading")}</p>
              ) : null}
              {!reviewsQuery.isLoading && (reviewsQuery.data?.length ?? 0) === 0 ? (
                <p className="review-workspace-empty">{t("nav.noReviews")}</p>
              ) : null}
              {reviewsQuery.data?.map((review) => (
                <div className="review-workspace" key={review.task_id}>
                  <NavLink
                    className={({ isActive }) =>
                      isActive ? "review-workspace__link active" : "review-workspace__link"
                    }
                    to={`/reviews/${review.task_id}`}
                  >
                    <span>{review.repository_name}</span>
                    <small>
                      {review.status.replaceAll("_", " ")} ·{" "}
                      {dateFormatter.format(new Date(review.created_at))}
                    </small>
                  </NavLink>
                  <button
                    aria-label={`${t("nav.deleteReview")} ${review.repository_name}`}
                    disabled={deleteMutation.isPending}
                    type="button"
                    onClick={() => handleDelete(review.task_id, review.repository_name)}
                  >
                    <Trash2 aria-hidden="true" />
                  </button>
                </div>
              ))}
            </div>
            <NavLink className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")} to="/runs">
              <History aria-hidden="true" />
              <span>Runs</span>
            </NavLink>
          </section>

          <div className="nav-section navigation-secondary">
            <p className="nav-label">Configuration</p>
            <NavLink className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")} to="/agents">
              <Bot aria-hidden="true" />
              <span>{t("nav.reviewAgents")}</span>
            </NavLink>
            <NavLink className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")} to="/capabilities">
              <Blocks aria-hidden="true" />
              <span>Capabilities</span>
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
          <div className="breadcrumb"><span>{t("nav.reviews")}</span><span aria-hidden="true">/</span><strong>{isNewReview ? t("nav.newReview") : "Workspace"}</strong></div>
          <span className="topbar-context">{t("app.context")}</span>
        </header>
        <main className="main-content"><Outlet /></main>
      </div>
    </div>
  );
}
