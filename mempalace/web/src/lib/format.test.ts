import { describe, expect, it } from "vitest";

import { formatInt, relativeTime, truncate } from "./format";

describe("formatInt", () => {
  it("uses thousand separators", () => {
    expect(formatInt(91205)).toBe("91,205");
  });
});

describe("truncate", () => {
  it("returns original when within max", () => {
    expect(truncate("hello", 10)).toBe("hello");
  });
  it("ellipsizes when over max", () => {
    expect(truncate("hello world", 6)).toBe("hello…");
  });
});

describe("relativeTime", () => {
  it("falls back to the input string when unparseable", () => {
    expect(relativeTime("not-a-date")).toBe("not-a-date");
  });
  it("returns seconds for very-recent timestamps", () => {
    const iso = new Date(Date.now() - 5_000).toISOString();
    expect(relativeTime(iso)).toMatch(/^\d+s ago$/);
  });
});
