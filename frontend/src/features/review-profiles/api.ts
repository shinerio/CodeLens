import { api } from "../../shared/api/client";
import type { ReviewerSelection } from "../reviews/types";
import type { ReviewProfile, SaveReviewProfileInput } from "./types";

type ReviewProfileDto = {
  profile_id: string;
  revision: number;
  name: string;
  is_default: boolean;
  reviewer_selection: ReviewerSelection;
  created_at: string;
  updated_at: string;
};

function fromDto(profile: ReviewProfileDto): ReviewProfile {
  return {
    id: profile.profile_id,
    revision: profile.revision,
    name: profile.name,
    isDefault: profile.is_default,
    strategy: {
      reviewerSelection:
        profile.reviewer_selection.mode === "adaptive"
          ? { mode: "adaptive" }
          : {
              mode: "fixed",
              reviewerVersions: profile.reviewer_selection.reviewer_versions,
            },
    },
    createdAt: profile.created_at,
    updatedAt: profile.updated_at,
  };
}

function toDto(input: SaveReviewProfileInput) {
  const selection = input.strategy.reviewerSelection;
  return {
    name: input.name,
    is_default: input.isDefault,
    reviewer_selection:
      selection.mode === "adaptive"
        ? { mode: "adaptive" as const }
        : { mode: "fixed" as const, reviewer_versions: [...selection.reviewerVersions] },
  };
}

export async function listReviewProfiles(): Promise<ReviewProfile[]> {
  return (await api<ReviewProfileDto[]>("/review-profiles")).map(fromDto);
}

export async function getReviewProfile(id: string): Promise<ReviewProfile> {
  const profile = (await listReviewProfiles()).find((candidate) => candidate.id === id);
  if (profile === undefined) {
    throw new Error("review_profile_not_found");
  }
  return profile;
}

export async function createReviewProfile(input: SaveReviewProfileInput): Promise<ReviewProfile> {
  return fromDto(await api<ReviewProfileDto>("/review-profiles", {
    method: "POST",
    body: JSON.stringify(toDto(input)),
  }));
}

export async function updateReviewProfile(
  profile: ReviewProfile,
  input: SaveReviewProfileInput,
): Promise<ReviewProfile> {
  return fromDto(await api<ReviewProfileDto>(`/review-profiles/${profile.id}`, {
    method: "PUT",
    body: JSON.stringify({ ...toDto(input), revision: profile.revision }),
  }));
}

export async function setDefaultReviewProfile(profile: ReviewProfile): Promise<ReviewProfile> {
  return updateReviewProfile(profile, {
    name: profile.name,
    isDefault: true,
    strategy: profile.strategy,
  });
}

export async function copyReviewProfile(id: string, name: string): Promise<ReviewProfile> {
  return fromDto(await api<ReviewProfileDto>(`/review-profiles/${id}/copies`, {
    method: "POST",
    body: JSON.stringify({ name }),
  }));
}

export async function deleteReviewProfile(id: string): Promise<void> {
  await api<void>(`/review-profiles/${id}`, { method: "DELETE" });
}
