import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it } from "vitest";

import { I18nProvider, detectLocale, useI18n } from "./i18n";

afterEach(cleanup);

function Probe() {
  const { locale, setLocale, t } = useI18n();
  return (
    <>
      <span>{t("nav.newReview")}</span>
      <button type="button" onClick={() => setLocale(locale === "en" ? "zh-CN" : "en")}>
        {t("app.languageSwitch")}
      </button>
    </>
  );
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

it("lets users switch the interface language", async () => {
  const user = userEvent.setup();
  render(
    <I18nProvider>
      <Probe />
    </I18nProvider>,
  );

  await user.click(screen.getByRole("button", { name: "中文" }));

  expect(screen.getByText("新建 Review")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "EN" })).toBeInTheDocument();
});
