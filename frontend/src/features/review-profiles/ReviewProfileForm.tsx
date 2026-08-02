import { useEffect, useState, type FormEvent } from "react";
import { useI18n } from "../../shared/i18n/i18n";

import type { ReviewerCatalogEntry } from "../catalog/types";
import type { ReviewStrategySnapshot } from "../reviews/types";
import { ReviewStrategyEditor } from "../review-strategy/ReviewStrategyEditor";
import { validateStrategy } from "../review-strategy/model";
import type { ReviewProfile, SaveReviewProfileInput } from "./types";

export function ReviewProfileForm({ profile, catalog, isSaving, error, onSave, onCancel, onReload, onDirtyChange }: { profile: ReviewProfile | null; catalog: readonly ReviewerCatalogEntry[]; isSaving: boolean; error: string | null; onSave: (input: SaveReviewProfileInput) => void; onCancel: () => void; onReload?: () => void; onDirtyChange?: (isDirty: boolean) => void }) {
  const { t } = useI18n();
  const [name, setName] = useState(profile?.name ?? "");
  const [strategy, setStrategy] = useState<ReviewStrategySnapshot>(
    profile?.strategy ?? {
      reviewerSelection: { mode: "adaptive" },
      budgetProfile: "standard",
    },
  );
  const [isDefault, setIsDefault] = useState(profile?.isDefault ?? false);
  const errors = validateStrategy(strategy, catalog);
  const initialName = profile?.name ?? "";
  const initialDefault = profile?.isDefault ?? false;
  const initialStrategy = JSON.stringify(profile?.strategy ?? {
    reviewerSelection: { mode: "adaptive" },
    budgetProfile: "standard",
  });
  const isDirty = name !== initialName || isDefault !== initialDefault || JSON.stringify(strategy) !== initialStrategy;
  useEffect(() => {
    onDirtyChange?.(isDirty);
    return () => onDirtyChange?.(false);
  }, [isDirty, onDirtyChange]);
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (name.trim() !== "" && errors.length === 0) onSave({ name: name.trim(), isDefault, strategy });
  }
  return (
    <form className="profile-form" onSubmit={submit}>
      <label><span>{t("profiles.name")}</span><input aria-label={t("profiles.name")} maxLength={120} value={name} onChange={(event) => setName(event.currentTarget.value)} /></label>
      <label className="profile-form__default"><input checked={isDefault} disabled={profile?.isDefault === true} type="checkbox" onChange={(event) => setIsDefault(event.currentTarget.checked)} /> {t("profiles.instanceDefault")}</label>
      <ReviewStrategyEditor catalog={catalog} validationErrors={errors} value={strategy} onChange={setStrategy} />
      {error !== null ? <div className="profile-form__error" role="alert"><p>{error}</p>{onReload === undefined ? null : <button type="button" onClick={onReload}>{t("profiles.reload")}</button>}</div> : null}
      <div className="profile-form__actions"><button type="button" onClick={onCancel}>{t("profiles.cancel")}</button><button disabled={isSaving || name.trim() === "" || errors.length > 0} type="submit">{t(isSaving ? "profiles.saving" : "profiles.save")}</button></div>
    </form>
  );
}
