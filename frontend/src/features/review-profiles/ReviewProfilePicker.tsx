import type { ReviewProfile } from "./types";
import { useI18n } from "../../shared/i18n/i18n";

export function ReviewProfilePicker({ profiles, value, isDisabled = false, onChange }: { profiles: readonly ReviewProfile[]; value: string; isDisabled?: boolean; onChange: (profile: ReviewProfile) => void }) {
  const { t } = useI18n();
  return (
    <label className="profile-picker">
      <span>{t("profiles.picker")}</span>
      <select
        aria-label={t("profiles.picker")}
        disabled={isDisabled}
        value={value}
        onChange={(event) => {
          const selected = profiles.find((profile) => profile.id === event.currentTarget.value);
          if (selected !== undefined) onChange(selected);
        }}
      >
        {profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}{profile.isDefault ? ` · ${t("profiles.defaultSuffix")}` : ""}</option>)}
      </select>
    </label>
  );
}
