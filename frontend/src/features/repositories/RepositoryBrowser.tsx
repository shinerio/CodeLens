import { useMutation } from "@tanstack/react-query";
import {
  ArrowLeft,
  Check,
  Folder,
  FolderGit2,
  HardDrive,
  X,
} from "lucide-react";
import { useEffect } from "react";

import { useI18n } from "../../shared/i18n/i18n";
import { browseDirectories } from "./api";
import type { DirectoryEntry } from "./types";
import "./RepositoryBrowser.css";

export function RepositoryBrowser({
  isOpen,
  onClose,
  onSelect,
}: {
  isOpen: boolean;
  onClose: () => void;
  onSelect: (path: string) => void;
}) {
  const { t } = useI18n();
  const browseMutation = useMutation({ mutationFn: browseDirectories });

  useEffect(() => {
    if (isOpen) {
      browseMutation.mutate(null);
    }
    // Reopening must always begin at platform roots rather than stale state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen]);

  if (!isOpen) {
    return null;
  }

  const listing = browseMutation.data;

  function handleEntry(entry: DirectoryEntry) {
    browseMutation.mutate(entry.path);
  }

  return (
    <div className="repository-browser-backdrop" role="presentation">
      <section
        aria-labelledby="repository-browser-title"
        aria-modal="true"
        className="repository-browser"
        role="dialog"
      >
        <header className="repository-browser__header">
          <div>
            <p>filesystem / navigator</p>
            <h2 id="repository-browser-title">{t("repository.browserTitle")}</h2>
            <span>{t("repository.browserSubtitle")}</span>
          </div>
          <button aria-label={t("common.close")} type="button" onClick={onClose}>
            <X aria-hidden="true" />
          </button>
        </header>

        <div className="repository-browser__location">
          <HardDrive aria-hidden="true" />
          <span>{t("repository.currentPath")}</span>
          <code>{listing?.current_path ?? t("repository.systemRoots")}</code>
          {listing?.current_is_git_repository ? (
            <button type="button" onClick={() => onSelect(listing.current_path ?? "")}>
              <Check aria-hidden="true" /> {t("repository.select")}
            </button>
          ) : null}
        </div>

        {browseMutation.isPending ? (
          <div className="repository-browser__empty">{t("common.loading")}</div>
        ) : null}
        {browseMutation.isError ? (
          <div className="repository-browser__error" role="alert">
            {browseMutation.error instanceof Error
              ? browseMutation.error.message
              : "Unable to browse directory."}
          </div>
        ) : null}

        {!browseMutation.isPending && listing !== undefined ? (
          <div className="repository-browser__body">
            {listing.current_path === null ? (
              <div className="repository-browser__roots">
                {listing.roots.map((root) => (
                  <button key={root} type="button" onClick={() => browseMutation.mutate(root)}>
                    <HardDrive aria-hidden="true" />
                    <span>{root}</span>
                  </button>
                ))}
              </div>
            ) : (
              <div className="repository-browser__directories">
                {listing.parent_path !== null ? (
                  <button
                    className="directory-row directory-row--parent"
                    type="button"
                    onClick={() => browseMutation.mutate(listing.parent_path)}
                  >
                    <ArrowLeft aria-hidden="true" />
                    <span>{t("repository.parent")}</span>
                    <code>..</code>
                  </button>
                ) : null}
                {listing.directories.map((entry) => {
                  const Icon = entry.is_git_repository ? FolderGit2 : Folder;
                  return (
                    <div className="directory-row" key={entry.path}>
                      <button type="button" onClick={() => handleEntry(entry)}>
                        <Icon aria-hidden="true" />
                        <span>{entry.name}</span>
                        {entry.is_git_repository ? (
                          <small>{t("repository.gitBadge")}</small>
                        ) : null}
                      </button>
                      {entry.is_git_repository ? (
                        <button
                          aria-label={`${t("repository.select")} ${entry.name}`}
                          className="directory-row__select"
                          type="button"
                          onClick={() => onSelect(entry.path)}
                        >
                          <Check aria-hidden="true" />
                        </button>
                      ) : null}
                    </div>
                  );
                })}
                {listing.directories.length === 0 ? (
                  <div className="repository-browser__empty">
                    {t("repository.emptyDirectory")}
                  </div>
                ) : null}
              </div>
            )}
            {listing.is_truncated ? (
              <p className="repository-browser__truncated">{t("repository.truncated")}</p>
            ) : null}
          </div>
        ) : null}
      </section>
    </div>
  );
}
