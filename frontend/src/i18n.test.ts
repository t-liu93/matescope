import { describe, expect, it } from "vitest";

import { suggestedLanguage } from "./i18n";

describe("browser language suggestion", () => {
  it("uses the first supported browser language", () => {
    expect(suggestedLanguage(["nl-NL", "en-US", "zh-CN"])).toBe("en");
    expect(suggestedLanguage(["nl-NL", "zh-CN", "en-US"])).toBe("zh");
  });

  it("falls back to English for other browser preferences", () => {
    expect(suggestedLanguage(["nl-NL", "en-US"])).toBe("en");
  });
});
