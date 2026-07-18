import { useQuery } from "@tanstack/react-query";
import { ExternalLink, Plus, RefreshCw, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { useI18n } from "../../shared/i18n/i18n";
import { listReviews } from "./api";
import "./RunListPage.css";

/** Lists persisted review runs and retains demo-only actions as explicit previews. */
export function RunListPage() {
  const { t } = useI18n();
  const [query, setQuery] = useState("");
  const reviewsQuery = useQuery({ queryKey: ["reviews"], queryFn: listReviews });
  const reviews = useMemo(() => (reviewsQuery.data ?? []).filter((review) => review.repository_name.toLowerCase().includes(query.toLowerCase())), [query, reviewsQuery.data]);

  function handleUnsupported() { window.alert(t("common.notSupported")); }

  return <section className="run-list-page">
    <header><div><p>Review / execution history</p><h1>Review runs</h1><span>Recent review and fix executions across trusted repositories.</span></div><div><Link to="/fix/demo">Fix preview</Link><button type="button" onClick={handleUnsupported}><RefreshCw aria-hidden="true" /> Refresh</button><Link to="/reviews/new"><Plus aria-hidden="true" /> New review</Link></div></header>
    <div className="run-list-page__toolbar"><label><Search aria-hidden="true" /><input aria-label="Search runs" placeholder="Search repository, branch, or run ID" value={query} onChange={(event) => setQuery(event.currentTarget.value)} /></label><span>{reviews.length} run{reviews.length === 1 ? "" : "s"}</span></div>
    <div className="run-list-page__table"><div className="run-list-page__row run-list-page__row--header"><span>Repository</span><span>Status</span><span>Scope</span><span>Created</span><span /></div>{reviewsQuery.isLoading ? <p>{t("common.loading")}</p> : null}{reviews.map((review) => <div className="run-list-page__row" key={review.task_id}><strong>{review.repository_name}<small>{review.task_id}</small></strong><span className="run-list-page__status">{review.status.replaceAll("_", " ")}</span><span>{review.scope_type}</span><time>{new Date(review.created_at).toLocaleString()}</time><Link aria-label={`Open ${review.repository_name}`} to={`/reviews/${review.task_id}`}><ExternalLink aria-hidden="true" /></Link></div>)}{!reviewsQuery.isLoading && reviews.length === 0 ? <p className="run-list-page__empty">No runs match this view.</p> : null}</div>
  </section>;
}
