import { ExternalLink } from "lucide-react";

import { useI18n } from "../../shared/i18n/i18n";
import type { PluginRecord } from "../plugins/types";
import type { ExportResultResponse } from "./api";

interface PluginPanelsProps {
  externalContext: Record<string, unknown> | null;
  plugins: PluginRecord[];
  exportHistory: ExportResultResponse[];
  findingsCount: number;
}

const CODEHUB_HOST_DEFAULT = "codehub-g.huawei.com";

function buildCodehubMrUrl(project: string, mrIid: number | string, host?: string): string {
  const h = host ?? CODEHUB_HOST_DEFAULT;
  return `https://${h}/${project}/-/merge_requests/${mrIid}`;
}

function formatExportTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export function PluginPanels({ externalContext, plugins, exportHistory, findingsCount }: PluginPanelsProps) {
  const { t } = useI18n();

  if (!externalContext && exportHistory.length === 0) {
    return null;
  }

  const platform = externalContext?.platform as string | undefined;
  const matchingPlugin = platform
    ? plugins.find((p) => p.manifest.platform === platform)
    : undefined;

  return (
    <>
      {externalContext && platform && (
        <article className="run-panel run-panel--wide">
          <h2>{t("run.pluginSource")}</h2>
          <dl className="run-summary">
            <div>
              <dt>{t("run.platform")}</dt>
              <dd>{platform}</dd>
            </div>
            {matchingPlugin && (
              <div>
                <dt>{t("run.plugin")}</dt>
                <dd>{matchingPlugin.manifest.name}</dd>
              </div>
            )}
            {platform === "codehub" && (
              <>
                {externalContext.project && (
                  <div>
                    <dt>{t("run.project")}</dt>
                    <dd><code>{String(externalContext.project)}</code></dd>
                  </div>
                )}
                {externalContext.merge_request && externalContext.project && (
                  <div>
                    <dt>{t("run.mergeRequest")}</dt>
                    <dd>
                      <a
                        href={buildCodehubMrUrl(
                          String(externalContext.project),
                          externalContext.merge_request as number | string,
                          externalContext.codehub_host as string | undefined,
                        )}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="plugin-panel__link"
                      >
                        !{String(externalContext.merge_request)}
                        <ExternalLink aria-hidden="true" />
                      </a>
                    </dd>
                  </div>
                )}
              </>
            )}
          </dl>
        </article>
      )}

      {exportHistory.length > 0 && (
        <article className="run-panel run-panel--wide">
          <h2>{t("run.exportHistory")}</h2>
          <div className="plugin-panel__exports">
            {exportHistory.map((entry, index) => (
              <div
                key={`${entry.plugin_id}-${entry.exported_at}-${index}`}
                className={`plugin-panel__export-entry ${entry.success ? "plugin-panel__export-entry--ok" : "plugin-panel__export-entry--err"}`}
              >
                <div className="plugin-panel__export-head">
                  <strong>{entry.plugin_id}</strong>
                  <time>{formatExportTime(entry.exported_at)}</time>
                </div>
                <span className="plugin-panel__export-status">
                  {!entry.success
                    ? t("plugins.exportFailed")
                    : findingsCount === 0
                      ? t("plugins.exportNoFindings")
                      : t("plugins.exportSuccess")}
                </span>
                {entry.error && (
                  <p className="plugin-panel__export-error">{entry.error}</p>
                )}
                {entry.output_path && (
                  <p className="plugin-panel__export-path">
                    <code>{entry.output_path}</code>
                  </p>
                )}
              </div>
            ))}
          </div>
        </article>
      )}
    </>
  );
}
