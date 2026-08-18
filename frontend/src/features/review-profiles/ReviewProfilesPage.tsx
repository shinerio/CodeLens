import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Plus, Star, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { useI18n } from "../../shared/i18n/i18n";

import { listReviewerCatalog } from "../catalog/api";
import { ReviewStrategySummary } from "../review-strategy/ReviewStrategySummary";
import {
  copyReviewProfile,
  createReviewProfile,
  deleteReviewProfile,
  listReviewProfiles,
  setDefaultReviewProfile,
  updateReviewProfile,
} from "./api";
import { ReviewProfileForm } from "./ReviewProfileForm";
import type { ReviewProfile, SaveReviewProfileInput } from "./types";
import "./ReviewProfilesPage.css";

const PROFILE_QUERY_KEY = ["review-profiles"] as const;

export function ReviewProfilesPage() {
  const { t } = useI18n();
  const queryClient = useQueryClient();
  const profilesQuery = useQuery({ queryKey: PROFILE_QUERY_KEY, queryFn: listReviewProfiles });
  const catalogQuery = useQuery({ queryKey: ["reviewer-catalog"], queryFn: listReviewerCatalog });
  const [editing, setEditing] = useState<ReviewProfile | null | "new">(null);
  const [copying, setCopying] = useState<ReviewProfile | null>(null);
  const [copyName, setCopyName] = useState("");
  const [isDirty, setIsDirty] = useState(false);
  const handleDirtyChange = useCallback((nextIsDirty: boolean) => setIsDirty(nextIsDirty), []);
  const refresh = () => queryClient.invalidateQueries({ queryKey: PROFILE_QUERY_KEY });

  useEffect(() => {
    if (!isDirty) return;
    const handleBeforeUnload = (event: BeforeUnloadEvent) => event.preventDefault();
    const handleNavigation = (event: MouseEvent) => {
      if (!(event.target instanceof Element)) return;
      const link = event.target.closest("a[href]");
      if (link !== null && !window.confirm(t("profiles.discardConfirm"))) {
        event.preventDefault();
      }
    };
    window.addEventListener("beforeunload", handleBeforeUnload);
    document.addEventListener("click", handleNavigation, true);
    return () => {
      window.removeEventListener("beforeunload", handleBeforeUnload);
      document.removeEventListener("click", handleNavigation, true);
    };
  }, [isDirty, t]);

  const saveMutation = useMutation({
    mutationFn: async (input: SaveReviewProfileInput) =>
      editing === "new"
        ? createReviewProfile(input)
        : editing === null
          ? Promise.reject(new Error("No profile selected"))
          : updateReviewProfile(editing, input),
    onSuccess: async () => {
      setEditing(null);
      setIsDirty(false);
      await refresh();
    },
  });
  const copyMutation = useMutation({
    mutationFn: ({ profile, name }: { profile: ReviewProfile; name: string }) =>
      copyReviewProfile(profile.id, name),
    onSuccess: async () => {
      setCopying(null);
      setCopyName("");
      await refresh();
    },
  });
  const deleteMutation = useMutation({ mutationFn: deleteReviewProfile, onSuccess: refresh });
  const defaultMutation = useMutation({ mutationFn: setDefaultReviewProfile, onSuccess: refresh });
  const profiles = profilesQuery.data ?? [];
  const defaultCount = profiles.filter((profile) => profile.isDefault).length;

  async function reloadEditedProfile() {
    if (editing === null || editing === "new") return;
    const result = await profilesQuery.refetch();
    const latest = result.data?.find((profile) => profile.id === editing.id);
    if (latest !== undefined) {
      saveMutation.reset();
      setEditing(latest);
      setIsDirty(false);
    }
  }

  function beginEditing(profile: ReviewProfile | "new") {
    saveMutation.reset();
    setCopying(null);
    setEditing(profile);
  }

  function beginCopy(profile: ReviewProfile) {
    setEditing(null);
    setCopying(profile);
    setCopyName("");
  }

  function submitCopy(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (copying !== null && copyName.trim() !== "") {
      copyMutation.mutate({ profile: copying, name: copyName.trim() });
    }
  }

  if (profilesQuery.isPending || catalogQuery.isPending) {
    return <section className="profiles-state">{t("profiles.loading")}</section>;
  }
  if (profilesQuery.isError || catalogQuery.isError) {
    return <section className="profiles-state" role="alert">{t("profiles.loadError")}</section>;
  }
  if (profiles.length === 0 || defaultCount !== 1) {
    return <section className="profiles-state" role="alert">{t("profiles.contractError")}</section>;
  }
  const saveError = saveMutation.isError
    ? saveMutation.error.message.includes("revision")
      ? t("profiles.revisionConflict")
      : saveMutation.error.message
    : null;

  return (
    <section className="profiles-page">
      <header>
        <div><p>{t("profiles.eyebrow")}</p><h1>{t("profiles.title")}</h1><span>{t("profiles.subtitle")}</span></div>
        <button onClick={() => beginEditing("new")}><Plus /> {t("profiles.new")}</button>
      </header>
      <div className="profiles-layout">
        <div className="profile-list">
          {profiles.map((profile) => (
            <article className={profile.isDefault ? "profile-card profile-card--default" : "profile-card"} key={profile.id}>
              <div><h2>{profile.name}</h2>{profile.isDefault ? <span className="profile-default"><Star /> {t("profiles.defaultSuffix")}</span> : null}</div>
              <ReviewStrategySummary strategy={profile.strategy} />
              <footer>
                <span>{t("profiles.revision", { revision: String(profile.revision) })}</span>
                {profile.isDefault ? null : <button onClick={() => defaultMutation.mutate(profile)}>{t("profiles.makeDefault")}</button>}
                <button onClick={() => beginEditing(profile)}>{t("profiles.edit")}</button>
                <button aria-label={t("profiles.duplicate", { name: profile.name })} onClick={() => beginCopy(profile)}><Copy /></button>
                <button
                  aria-label={profile.isDefault ? t("profiles.deleteDefaultBlocked", { name: profile.name }) : t("profiles.delete", { name: profile.name })}
                  disabled={profile.isDefault}
                  title={profile.isDefault ? t("profiles.deleteDefaultGuidance") : undefined}
                  onClick={() => {
                    if (window.confirm(t("profiles.deleteConfirm", { name: profile.name }))) {
                      deleteMutation.mutate(profile.id);
                    }
                  }}
                ><Trash2 /></button>
              </footer>
            </article>
          ))}
        </div>
        <aside className="profile-editor-panel">
          {copying !== null ? (
            <form className="profile-copy-form" onSubmit={submitCopy}>
              <p>{t("profiles.copyDescription", { name: copying.name })}</p>
              <label><span>{t("profiles.copyName")}</span><input aria-label={t("profiles.copyName")} maxLength={120} value={copyName} onChange={(event) => setCopyName(event.currentTarget.value)} /></label>
              {copyMutation.isError ? <p className="profile-form__error" role="alert">{copyMutation.error.message}</p> : null}
              <div className="profile-form__actions"><button type="button" onClick={() => setCopying(null)}>{t("profiles.cancel")}</button><button disabled={copyMutation.isPending || copyName.trim() === ""} type="submit">{t("profiles.createCopy")}</button></div>
            </form>
          ) : editing === null ? (
            <div className="profile-editor-empty"><strong>{t("profiles.select")}</strong><p>{t("profiles.snapshotNote")}</p></div>
          ) : (
            <ReviewProfileForm
              key={editing === "new" ? "new" : `${editing.id}:${editing.revision}`}
              catalog={catalogQuery.data ?? []}
              error={saveError}
              isSaving={saveMutation.isPending}
              profile={editing === "new" ? null : editing}
              onCancel={() => { setEditing(null); setIsDirty(false); }}
              onDirtyChange={handleDirtyChange}
              onReload={editing === "new" ? undefined : reloadEditedProfile}
              onSave={(input) => saveMutation.mutate(input)}
            />
          )}
        </aside>
      </div>
    </section>
  );
}
