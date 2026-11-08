// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// 布局与样式护栏：只做断言、不做像素比对，因此可以跨机器跑在 CI 里。
// 视觉基线（visual.spec.ts）绑定生成机器的字体渲染，只适合在本机或固定镜像内跑。
//
// 覆盖四件事：
// 1. reset 生效：html/body 的计算盒模型是 border-box；
// 2. 级联层顺序正确，且 utilities 真的能覆盖 components；
// 3. 1440 / 1200 / 1024 / 960 下页面没有横向溢出，窄宽度收起上下文列；
// 4. 侧边栏收起可用、面板隐藏、状态持久化。

import { expect, test, type Page } from "@playwright/test";

async function openProject(page: Page) {
  await page.goto("/");
  const project = page.locator(".project-item").first();
  await expect(project).toBeVisible();
  await project.click();
  await expect(page.locator(".room-list-row")).toHaveCount(4);
}

test.describe("样式护栏", () => {
  test("reset：html/body 计算盒模型为 border-box", async ({ page }) => {
    await page.goto("/");
    const boxes = await page.evaluate(() => ({
      html: getComputedStyle(document.documentElement).boxSizing,
      body: getComputedStyle(document.body).boxSizing,
    }));
    expect(boxes).toEqual({ html: "border-box", body: "border-box" });
  });

  test("级联：层顺序固定且 utilities 覆盖 components", async ({ page }) => {
    await page.goto("/");

    const layers = await page.evaluate(() =>
      [...document.styleSheets]
        .flatMap((sheet) => {
          try {
            return [...sheet.cssRules].map((rule) => rule.cssText);
          } catch {
            return [];
          }
        })
        .filter((text) => text.startsWith("@layer "))
        .map((text) => text.slice(0, 120)),
    );
    expect(
      layers.some((text) => text.startsWith("@layer reset, tokens, base, components, utilities")),
    ).toBe(true);

    // 用一段合成样式验证层顺序真的生效：同特异性下 utilities 必须胜出。
    const color = await page.evaluate(() => {
      const style = document.createElement("style");
      style.textContent =
        "@layer components{.layer-probe{color:rgb(1,2,3)}}@layer utilities{.layer-probe{color:rgb(4,5,6)}}";
      document.head.append(style);
      const probe = document.createElement("div");
      probe.className = "layer-probe";
      document.body.append(probe);
      const value = getComputedStyle(probe).color;
      probe.remove();
      style.remove();
      return value;
    });
    expect(color).toBe("rgb(4, 5, 6)");
  });

  for (const viewport of [
    { name: "1600×900", width: 1600, height: 900 },
    { name: "1440×900", width: 1440, height: 900 },
    { name: "1200×800", width: 1200, height: 800 },
    { name: "1024×800", width: 1024, height: 800 },
    { name: "960×800", width: 960, height: 800 },
  ]) {
    test(`布局：${viewport.name} 无横向溢出`, async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await openProject(page);
      const metrics = await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
      }));
      expect(metrics.scrollWidth).toBeLessThanOrEqual(metrics.clientWidth);
    });
  }

  test("1024：上下文列是窄轨，主区不少于视口的 60%", async ({ page }) => {
    await page.setViewportSize({ width: 1024, height: 800 });
    await openProject(page);
    const metrics = await page.evaluate(() => {
      const root = Number.parseFloat(getComputedStyle(document.documentElement).fontSize);
      const shell = document.querySelector(".workspace-shell");
      const main = document.querySelector(".workspace-main");
      const context = document.querySelector(".right-sidebar");
      if (!shell || !main || !context) return null;
      const columns = getComputedStyle(shell)
        .gridTemplateColumns.split(" ")
        .map((value) => Number.parseFloat(value));
      return {
        rail: 2.25 * root,
        context: context.getBoundingClientRect().width,
        column: columns[3] ?? -1,
        main: main.getBoundingClientRect().width,
        viewport: window.innerWidth,
      };
    });
    expect(metrics).not.toBeNull();
    expect(Math.abs((metrics?.context ?? 0) - (metrics?.rail ?? 0))).toBeLessThan(1);
    expect(Math.abs((metrics?.column ?? 0) - (metrics?.rail ?? 0))).toBeLessThan(1);
    expect((metrics?.main ?? 0) / (metrics?.viewport ?? 1)).toBeGreaterThanOrEqual(0.6);
  });

  test("960：房间栏占列，上下文栏不占列", async ({ page }) => {
    await page.setViewportSize({ width: 960, height: 800 });
    await openProject(page);
    const columns = await page.evaluate(() => {
      const shell = document.querySelector(".workspace-shell");
      if (!shell) return null;
      return getComputedStyle(shell)
        .gridTemplateColumns.split(" ")
        .map((value) => Number.parseFloat(value));
    });
    expect(columns).toHaveLength(4);
    expect(columns?.[1] ?? 0).toBeGreaterThan(100);
    expect(columns?.[3] ?? -1).toBe(0);
    await expect(page.locator("#room-sidebar-panel")).toBeVisible();
    await expect(page.locator("#context-sidebar-panel")).toBeHidden();
  });
});

test.describe("侧边栏护栏", () => {
  test("左侧收起：面板隐藏、状态持久化、可再次展开", async ({ page }) => {
    await page.goto("/");
    const shell = page.locator(".workspace-shell");
    const panel = page.locator("#room-sidebar-panel");
    const toggle = page.locator('[aria-controls="room-sidebar-panel"]');

    await expect(panel).toBeVisible();
    await toggle.click();
    await expect(shell).toHaveClass(/room-sidebar-collapsed/);
    await expect(panel).toBeHidden();
    expect(
      await page.evaluate(() => localStorage.getItem("ghrah.workspace.room-sidebar.collapsed")),
    ).toBe("1");

    await page.reload();
    await expect(shell).toHaveClass(/room-sidebar-collapsed/);
    await expect(panel).toBeHidden();

    await toggle.click();
    await expect(shell).not.toHaveClass(/room-sidebar-collapsed/);
    await expect(panel).toBeVisible();
    expect(
      await page.evaluate(() => localStorage.getItem("ghrah.workspace.room-sidebar.collapsed")),
    ).toBe("0");
  });

  test("两侧收起互不影响", async ({ page }) => {
    await page.goto("/");
    const shell = page.locator(".workspace-shell");
    await page.locator('[aria-controls="context-sidebar-panel"]').click();
    await expect(shell).toHaveClass(/context-sidebar-collapsed/);
    await expect(shell).not.toHaveClass(/room-sidebar-collapsed/);
    await expect(page.locator("#context-sidebar-panel")).toBeHidden();
    await expect(page.locator("#room-sidebar-panel")).toBeVisible();
  });
});
