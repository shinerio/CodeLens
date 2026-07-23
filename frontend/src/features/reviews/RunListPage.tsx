import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Plus, RefreshCw, Search, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { useI18n, type TranslationKey } from "../../shared/i18n/i18n";
import { deleteReview, listReviews } from "./api";
import "./RunListPage.css";
import "./RunListPageActions.css";

const REVIEW_STATUS_KEYS: Readonly<Record<string, TranslationKey>> = {
  created: "status.created",
  queued: "status.queued",
  running: "status.running",
  completed: "status.completed",
  partial: "status.partial",
  failed: "status.failed",
  canceled: "status.canceled",
  cancellation_requested: "status.cancelRequested",
};

const SCOPE_KEYS: Readonly<Record<string, TranslationKey>> = {
  branch: "runs.scopeBranch",
  commit: "runs.scopeCommit",
  uncommitted: "runs.scopeUncommitted",
  full: "runs.scopeFullRepository",
};

/** Lists persisted review runs and retains demo-only actions as explicit previews. */
export function RunListPage() {
  const { locale, t } = useI18n();
  const queryClient = useQueryClient();
  const [query, setQuery] = useState("");
  const [selectedTaskIds, setSelectedTaskIds] = useState<ReadonlySet<string>>(new Set());
  const reviewsQuery = useQuery({ queryKey: ["reviews"], queryFn: listReviews });
  const reviews = useMemo(() => (reviewsQuery.data ?? []).filter((review) => review.repository_name.toLowerCase().includes(query.toLowerCase())), [query, reviewsQuery.data]);
  const deleteMutation = useMutation({
    mutationFn: async (taskIds: string | readonly string[]) => {
      await Promise.all((typeof taskIds === "string" ? [taskIds] : taskIds).map(deleteReview));
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["reviews"] });
    },
  });

  const visibleTaskIds = reviews.map((review) => review.task_id);
  const allVisibleSelected = visibleTaskIds.length > 0 && visibleTaskIds.every((taskId) => selectedTaskIds.has(taskId));

  function toggleTask(taskId: string) {
    setSelectedTaskIds((current) => {
      const next = new Set(current);
      if (next.has(taskId)) next.delete(taskId); else next.add(taskId);
      return next;
    });
  }

  function toggleVisibleTasks() {
    setSelectedTaskIds((current) => {
      const next = new Set(current);
      if (allVisibleSelected) visibleTaskIds.forEach((taskId) => next.delete(taskId));
      else visibleTaskIds.forEach((taskId) => next.add(taskId));
      return next;
    });
  }

  async function refreshReviews() {
    await reviewsQuery.refetch();
  }

  function handleDelete(taskId: string, repositoryName: string) {
    if (window.confirm(t("nav.deleteConfirm", { name: repositoryName }))) {
      deleteMutation.mutate(taskId);
    }
  }

  function handleBatchDelete() {
    if (selectedTaskIds.size === 0) return;
    const label = locale === "zh-CN" ? `删除已选的 ${selectedTaskIds.size} 条记录？` : `Delete ${selectedTaskIds.size} selected reviews?`;
    if (!window.confirm(label)) return;
    deleteMutation.mutate(Array.from(selectedTaskIds), {
      onSuccess: () => setSelectedTaskIds(new Set()),
    });
  }

  const runCountKey = reviews.length === 1 ? "runs.count" : "runs.countPlural";

  return <section className="run-list-page">
    <header><div><p>{t("runs.eyebrow")}</p><h1>{t("runs.title")}</h1><span>{t("runs.subtitle")}</span></div><div><Link to="/fix/demo">{t("runs.fixPreview")}</Link><button type="button" onClick={() => void refreshReviews()} disabled={reviewsQuery.isFetching}><RefreshCw aria-hidden="true" /> {t("runs.refresh")}</button><Link to="/reviews/new"><Plus aria-hidden="true" /> {t("nav.newReview")}</Link></div></header>
    <div className="run-list-page__toolbar"><label><Search aria-hidden="true" /><input aria-label={t("runs.search")} placeholder={t("runs.searchPlaceholder")} value={query} onChange={(event) => setQuery(event.currentTarget.value)} /></label>{selectedTaskIds.size > 0 ? <button className="run-list-page__batch-delete" type="button" disabled={deleteMutation.isPending} onClick={handleBatchDelete}><Trash2 aria-hidden="true" /> {locale === "zh-CN" ? `删除已选 (${selectedTaskIds.size})` : `Delete selected (${selectedTaskIds.size})`}</button> : null}<span>{t(runCountKey, { count: reviews.length })}</span></div>
    {deleteMutation.isError ? <p className="run-list-page__error" role="alert">{deleteMutation.error instanceof Error ? deleteMutation.error.message : t("runs.unableDelete")}</p> : null}
    <div className="run-list-page__table"><div className="run-list-page__row run-list-page__row--header"><button className="run-list-page__select" aria-label={locale === "zh-CN" ? "选择全部记录" : "Select all reviews"} aria-pressed={allVisibleSelected} data-selected={allVisibleSelected} type="button" onClick={toggleVisibleTasks} /><span>{t("runs.repository")}</span><span>{t("runs.status")}</span><span>{t("runs.scope")}</span><span>{t("runs.created")}</span><span /></div>{reviewsQuery.isLoading ? <p>{t("common.loading")}</p> : null}{reviews.map((review) => { const isSelected = selectedTaskIds.has(review.task_id); return <div className="run-list-page__row" key={review.task_id}><button className="run-list-page__select" aria-label={locale === "zh-CN" ? `选择 ${review.repository_name}` : `Select ${review.repository_name}`} aria-pressed={isSelected} data-selected={isSelected} type="button" onClick={() => toggleTask(review.task_id)} /><strong>{review.repository_name}<small>{review.task_id}</small></strong><span className="run-list-page__status">{REVIEW_STATUS_KEYS[review.status] === undefined ? review.status.replaceAll("_", " ") : t(REVIEW_STATUS_KEYS[review.status])}</span><span>{SCOPE_KEYS[review.scope_type] === undefined ? review.scope_type : t(SCOPE_KEYS[review.scope_type])}</span><time>{new Date(review.created_at).toLocaleString(locale)}</time><span className="run-list-page__actions"><Link aria-label={t("runs.open", { name: review.repository_name })} to={`/runs/${review.task_id}`}><ExternalLink aria-hidden="true" /></Link><button aria-label={`${t("nav.deleteReview")} ${review.repository_name}`} className="run-list-page__delete" disabled={deleteMutation.isPending} type="button" onClick={() => handleDelete(review.task_id, review.repository_name)}><Trash2 aria-hidden="true" /></button></span></div>; })}{!reviewsQuery.isLoading && reviews.length === 0 ? <p className="run-list-page__empty">{t("runs.empty")}</p> : null}</div>
  </section>;
}
