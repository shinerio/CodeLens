import { afterEach, expect, it, vi } from "vitest";

import {
  createReviewProfile,
  getReviewProfile,
  listReviewProfiles,
  setDefaultReviewProfile,
  updateReviewProfile,
} from "./api";

const dto = {
  profile_id: "profile-1",
  revision: 2,
  name: "Security",
  is_default: true,
  reviewer_selection: { mode: "adaptive" },
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-02T00:00:00Z",
};

afterEach(() => vi.unstubAllGlobals());

it("maps profile DTOs into camel-case strategy snapshots", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify([dto]), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  })));
  expect(await listReviewProfiles()).toEqual([
    expect.objectContaining({
      id: "profile-1",
      isDefault: true,
      strategy: { reviewerSelection: { mode: "adaptive" } },
    }),
  ]);
});

it("uses revision-aware writes and stable snake-case boundaries", async () => {
  const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(
    new Response(JSON.stringify(dto), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  ));
  vi.stubGlobal("fetch", fetchMock);
  const profile = (await createReviewProfile({
    name: "Security",
    isDefault: true,
    strategy: { reviewerSelection: { mode: "adaptive" } },
  }));
  await updateReviewProfile(profile, {
    name: "Security",
    isDefault: true,
    strategy: { reviewerSelection: { mode: "adaptive" } },
  });
  const updateBody = JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body));
  expect(updateBody).toMatchObject({ revision: 2 });
});

it("gets one profile and switches the default with its loaded revision", async () => {
  const nonDefaultDto = { ...dto, is_default: false };
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify([nonDefaultDto]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }))
    .mockResolvedValueOnce(new Response(JSON.stringify(dto), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }));
  vi.stubGlobal("fetch", fetchMock);

  const profile = await getReviewProfile("profile-1");
  await setDefaultReviewProfile(profile);

  expect(fetchMock.mock.calls[1]?.[0]).toBe("/api/review-profiles/profile-1");
  expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))).toMatchObject({
    revision: 2,
    is_default: true,
  });
});
