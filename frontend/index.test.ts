import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

describe("browser metadata", () => {
  it("uses the CodeLens product name and icon", async () => {
    const indexHtml = await readFile(resolve(process.cwd(), "index.html"), "utf8");
    const document = new DOMParser().parseFromString(indexHtml, "text/html");
    const favicon = document.querySelector<HTMLLinkElement>('link[rel="icon"]');

    expect(document.title).toBe("CodeLens");
    expect(favicon?.getAttribute("type")).toBe("image/png");
    expect(favicon?.getAttribute("href")).toBe("./icons/CodeLens-favicon.png?v=2");
  });
});
