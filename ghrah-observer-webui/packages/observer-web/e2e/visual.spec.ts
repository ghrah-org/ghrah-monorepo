// 观察端视觉回归：4 个关键界面 × 2 种宽度 × 2 档字号，另加侧边栏收起状态。
//
// 数据来自 @ghrah/mock-server 的 demo 场景，并用 --replay-on-connect 保证“页面晚连也能
// 拿到完整状态”，因此截图不依赖等待时长；--no-chains 关掉了随时间追加的 ActionChain
// 事件，节点数量固定。时间文本（ActionChain 节点的 HH:MM:SS）在截图时用 mask 遮掉。
//
// 基线目录：e2e/visual.spec.ts-snapshots/（Playwright 默认命名，含平台后缀）。
// 更新基线：pnpm test:visual:update；基线差异必须在改动说明里逐一确认。

import { expect, test, type Page } from "@playwright/test";

const VIEWPORTS = [
  { name: "wide", width: 1600, height: 900 },
  { name: "narrow", width: 1024, height: 800 },
] as const;

// 1 = 100%，1.1 = 110%；字号由 localStorage 的 ghrah-font-scale 驱动。
const SCALES = ["1", "1.1"] as const;

interface PrepareOptions {
  scale?: string;
  sidebar?: "left-collapsed" | "both-collapsed";
}

async function prepare(page: Page, options: PrepareOptions = {}) {
  await page.addInitScript(({ scale, sidebar }) => {
    localStorage.clear();
    localStorage.setItem("theme", "light");
    // 固定中文，避免 runner 的 navigator.language 不同导致截图文本漂移
    localStorage.setItem("ghrah-locale", "zh-CN");
    if (scale) localStorage.setItem("ghrah-font-scale", scale);
    if (sidebar) localStorage.setItem("ghrah.workspace.room-sidebar.collapsed", "1");
    if (sidebar === "both-collapsed") {
      localStorage.setItem("ghrah.workspace.context-sidebar.collapsed", "1");
    }
  }, options);
}

/** 打开页面并选中唯一的 project；选中后房间列表与 Agent 列表才会渲染。 */
async function openProject(page: Page) {
  await page.goto("/");
  const project = page.locator(".project-item").first();
  await expect(project).toBeVisible();
  await project.click();
  await expect(page.locator(".room-list-row")).toHaveCount(4);
  await expect(page.locator(".agent-list li")).toHaveCount(4);
  // demo 场景的 HITL 在启动后约 12.6s 才产生；摘要条出现即视为事件已到，避免截图卡在中间态。
  await expect(page.locator(".hitl-bar")).toBeVisible();
}

async function shot(page: Page, name: string) {
  // 截图前把鼠标移到固定位置，避免残留 hover 态造成差异。
  await page.mouse.move(1580, 10);
  await expect(page).toHaveScreenshot(`${name}.png`, {
    mask: [page.locator(".ac-node-meta")],
  });
}

for (const viewport of VIEWPORTS) {
  for (const scale of SCALES) {
    test.describe(`${viewport.name} ${viewport.width}×${viewport.height} @ ${Math.round(Number(scale) * 100)}%`, () => {
      test.use({ viewport: { width: viewport.width, height: viewport.height } });

      test.beforeEach(async ({ page }) => {
        await prepare(page, { scale });
      });

      test("dashboard：选中 project 的空工作区", async ({ page }) => {
        await openProject(page);
        await shot(page, `${viewport.name}-${scale}-dashboard`);
      });

      test("chat：room 会话", async ({ page }) => {
        await openProject(page);
        await page.locator(".room-list-row", { hasText: "architecture" }).click();
        await expect(page.locator(".workspace-tab", { hasText: "architecture" })).toBeVisible();
        await shot(page, `${viewport.name}-${scale}-chat`);
      });

      test("action-chain：Agent 的 ActionChain", async ({ page }) => {
        await openProject(page);
        await page.locator(".agent-list li", { hasText: "architect" }).click();
        await expect(page.locator(".workspace-tab", { hasText: "architect" })).toBeVisible();
        // 分支投影在 HITL 之后（约 13.8s）到达，等 retry-2 出现再截图
        await expect(page.locator(".chain-branch-select")).toContainText("retry-2");
        await expect(page.locator(".ac-node")).toHaveCount(1);
        await shot(page, `${viewport.name}-${scale}-action-chain`);
      });

      test("settings：Project 设置", async ({ page }) => {
        await openProject(page);
        await page.locator(".project-fixed-nav .sidebar-row", { hasText: "Project 设置" }).click();
        await expect(page.locator(".workspace-tab", { hasText: "设置" })).toBeVisible();
        await shot(page, `${viewport.name}-${scale}-settings`);
      });
    });
  }
}

// 侧边栏三态：展开态由上面的 dashboard 基线覆盖，这里补收起与双收起。
test.describe("侧边栏收起状态（1600×900 / 100%）", () => {
  test.use({ viewport: { width: 1600, height: 900 } });

  test("左侧收起", async ({ page }) => {
    await prepare(page, { scale: "1", sidebar: "left-collapsed" });
    await openProject(page);
    await shot(page, "wide-1-sidebar-left-collapsed");
  });

  test("两侧都收起", async ({ page }) => {
    await prepare(page, { scale: "1", sidebar: "both-collapsed" });
    await openProject(page);
    await shot(page, "wide-1-sidebar-both-collapsed");
  });
});
