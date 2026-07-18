import { render, screen } from "@testing-library/react";
import { expect, it } from "vitest";

import { I18nProvider, detectLocale, useI18n } from "./i18n";

function Probe() {
  const { t } = useI18n();
  return <span>{t("nav.newReview")}</span>;
}

it("detects Chinese system locales and defaults every other locale to English", () => {
  expect(detectLocale(["zh-CN", "en-US"])).toBe("zh-CN");
  expect(detectLocale(["zh-TW"])).toBe("zh-CN");
  expect(detectLocale(["en-US"])).toBe("en");
  expect(detectLocale([])).toBe("en");
});

it("renders the Chinese interface dictionary when the system locale is Chinese", () => {
  render(
    <I18nProvider locale="zh-CN">
      <Probe />
    </I18nProvider>,
  );

  expect(screen.getByText("新建 Review")).toBeInTheDocument();
});
