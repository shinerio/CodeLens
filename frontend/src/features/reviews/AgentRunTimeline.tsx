import type { ReviewPlanProjection } from "./types";
import { useI18n } from "../../shared/i18n/i18n";

const ROLE_LABELS = {
  planner: "execution.rolePlanner",
  reviewer: "execution.roleReviewer",
  verifier: "execution.roleVerifier",
  deduplicator: "execution.roleDeduplicator",
} as const;

export function AgentRunTimeline({ plan }: { plan: ReviewPlanProjection | null }) {
  const { t } = useI18n();
  if (plan === null) return <p className="run-empty">{t("execution.planPending")}</p>;
  return (
    <ol className="agent-run-timeline">
      {plan.nodes.map((node) => (
        <li key={node.node_id}>
          <span>{node.pass_index}</span>
          <div><strong>{t(ROLE_LABELS[node.node_type])}</strong><code>{node.agent_reference}</code></div>
          <small>{node.depends_on.length === 0 ? t("execution.entryNode") : t("execution.dependencies", { count: String(node.depends_on.length) })}</small>
        </li>
      ))}
    </ol>
  );
}
