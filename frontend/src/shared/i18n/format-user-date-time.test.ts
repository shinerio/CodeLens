import { afterEach, expect, it, vi } from "vitest";

import { formatUserDateTime } from "./format-user-date-time";

afterEach(() => vi.restoreAllMocks());

it("uses the browser's resolved system time zone by default", () => {
  const resolvedOptions = new Intl.DateTimeFormat().resolvedOptions();
  vi.spyOn(Intl.DateTimeFormat.prototype, "resolvedOptions").mockReturnValue({
    ...resolvedOptions,
    timeZone: "Asia/Shanghai",
  });

  expect(formatUserDateTime("2026-07-18T12:00:00Z", "en")).toContain("8:00:00 PM");
});

it("formats the same timestamp differently across system time zones", () => {
  const timestamp = "2026-07-18T12:00:00Z";

  expect(formatUserDateTime(timestamp, "en", "Asia/Shanghai")).toContain("8:00:00 PM");
  expect(formatUserDateTime(timestamp, "en", "America/New_York")).toContain("8:00:00 AM");
});

it("treats legacy API timestamps without an offset as UTC", () => {
  expect(formatUserDateTime("2026-07-18T12:00:00", "en", "Asia/Shanghai")).toContain(
    "8:00:00 PM",
  );
});
