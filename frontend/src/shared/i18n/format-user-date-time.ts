import type { Locale } from "./i18n";

/** Format an API timestamp in the browser's system time zone. */
export function formatUserDateTime(
  timestamp: string,
  locale: Locale,
  timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone,
): string {
  const utcTimestamp = /(?:Z|[+-]\d{2}:\d{2})$/i.test(timestamp) ? timestamp : `${timestamp}Z`;
  return new Intl.DateTimeFormat(locale, {
    dateStyle: "medium",
    timeStyle: "medium",
    timeZone,
  }).format(new Date(utcTimestamp));
}
