/**
 * Guards for the translation catalogue.
 *
 * A missing key silently renders its own name in the UI, which is easy to miss in
 * review. These tests fail the build instead.
 */

import { describe, expect, it } from "vitest";
import { MESSAGES, interpolate, translate } from "./index";
import { translateWarning } from "./warnings";

const entries = Object.entries(MESSAGES as unknown as Record<string, { en: string; zh: string }>);

describe("translation catalogue", () => {
  it("is not empty", () => {
    expect(entries.length).toBeGreaterThan(200);
  });

  it("has a Chinese and an English string for every key", () => {
    const broken = entries
      .filter(([, value]) => !value.zh || !value.en || !value.zh.trim() || !value.en.trim())
      .map(([key]) => key);
    expect(broken).toEqual([]);
  });

  it("uses the same placeholders in both languages", () => {
    const placeholder = /\{(\w+)\}/g;
    const mismatched = entries
      .filter(([, value]) => {
        const en = new Set(Array.from(value.en.matchAll(placeholder), (m) => m[1]));
        const zh = new Set(Array.from(value.zh.matchAll(placeholder), (m) => m[1]));
        if (en.size !== zh.size) return true;
        return [...en].some((name) => !zh.has(name));
      })
      .map(([key]) => key);
    expect(mismatched).toEqual([]);
  });

  it("does not leave Chinese strings inside the English catalogue", () => {
    // Proper nouns and labels like "中文" / "EN" are allowed.
    const allowed = new Set(["language.chinese", "language.english"]);
    const suspicious = entries
      .filter(([key, value]) => !allowed.has(key) && /[\u4e00-\u9fff]/.test(value.en))
      .map(([key]) => key);
    expect(suspicious).toEqual([]);
  });

  it("actually differs between the two languages", () => {
    // Brand names, product names, units and the language labels themselves are the
    // same in both catalogues; anything else being identical means a missed string.
    const intentionallyIdentical = new Set([
      "language.chinese",
      "language.english",
      "shell.dockerOk",
      "import.tabGithub",
      "overview.docker",
      "overview.chromium",
      "overview.ffmpeg",
      "assetsPanel.gif",
      "providers.baseUrl",
      "settings.chromium",
      "settings.mb",
      "settings.dockerCli",
      "settings.compose",
      "settings.playwrightChromium",
    ]);
    const identical = entries
      .filter(([key, value]) => value.zh === value.en && !intentionallyIdentical.has(key))
      .map(([key]) => key);
    expect(identical).toEqual([]);
  });
});

describe("translate()", () => {
  it("returns Chinese by default", () => {
    expect(translate("zh", "nav.overview")).toBe("总览");
  });

  it("returns English when asked", () => {
    expect(translate("en", "nav.overview")).toBe("Overview");
  });

  it("interpolates named placeholders", () => {
    expect(translate("zh", "common.items", { count: 7 })).toBe("7 项");
    expect(translate("en", "common.items", { count: 7 })).toBe("7 items");
  });

  it("keeps unknown placeholders untouched", () => {
    expect(interpolate("a {x} b {y}", { x: 1 })).toBe("a 1 b {y}");
  });

  it("falls back to the key when it does not exist", () => {
    expect(translate("zh", "no.such.key")).toBe("no.such.key");
  });

  it("falls back to the key for a key that is not registered", () => {
    expect(translate("zh", "custom.partial")).toBe("custom.partial");
    expect(translate("en", "custom.partial")).toBe("custom.partial");
  });
});

describe("translateWarning()", () => {
  const t = (key: string) => translate("zh", key);

  it("translates a known backend warning", () => {
    expect(translateWarning("zh", "Docker daemon is stopped: container deployment will fail until it starts.", t)).toBe(
      "Docker 守护进程已停止：启动前容器化部署都会失败。",
    );
  });

  it("also matches the daemon wording used by /api/system/docker", () => {
    expect(translateWarning("zh", "Docker daemon is not running", t)).toBe(
      "Docker 守护进程已停止：启动前容器化部署都会失败。",
    );
  });

  it("keeps unknown warnings verbatim so nothing is hidden", () => {
    const unknown = "Something the backend just invented";
    expect(translateWarning("zh", unknown, t)).toBe(unknown);
  });

  it("never rewrites English output", () => {
    const english = "Docker daemon is stopped: container deployment will fail until it starts.";
    expect(translateWarning("en", english, t)).toBe(english);
  });
});
