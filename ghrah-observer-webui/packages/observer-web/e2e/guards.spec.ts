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

for (const width of [1600, 1024]) {
  for (const scale of [1, 1.1]) {
    test(`聊天输入框：${width} / ${scale} 排版、列宽与自动增高`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.addInitScript((value) => {
        localStorage.setItem("ghrah-font-scale", String(value));
      }, scale);
      await openProject(page);
      await page.locator(".room-list-row", { hasText: "architecture" }).click();
      const input = page.locator("textarea.field-multiline-chat");
      await expect(input).toBeVisible();
      for (const lines of [1, 2, 6, 12, 1]) {
        await input.fill(Array(lines).fill("聊天输入测试 mixed text 😀").join("\n"));
        await expect
          .poll(() =>
            input.evaluate((element) => {
              const style = getComputedStyle(element);
              return (
                element.scrollHeight <= element.clientHeight + 1 ||
                element.getBoundingClientRect().height >= parseFloat(style.maxHeight) - 1
              );
            }),
          )
          .toBe(true);
      }
      const metrics = await input.evaluate((element) => {
        const style = getComputedStyle(element);
        const rootStyle = getComputedStyle(document.documentElement);
        const remPx = Number.parseFloat(rootStyle.fontSize);
        // 列宽上限来自根级宽度 token。消息流的 max-width 是 min()/calc()
        // 表达式，getComputedStyle 会原样返回、parseFloat 得到 NaN，因此直接
        // 读 token 再折算：与 chat-panel 的 RESIZER_GUTTER_REM 同步留手柄位。
        const tokenPx = (name: string) =>
          Number.parseFloat(rootStyle.getPropertyValue(name)) * remPx;
        const RESIZER_GUTTER_REM = 3;
        const columns = [...document.querySelectorAll(".chat-conversation, .chat-composer")].map(
          (column) => {
            const rect = column.getBoundingClientRect();
            const parent = column.parentElement!.getBoundingClientRect();
            const computed = getComputedStyle(column);
            const isConversation = column.classList.contains("chat-conversation");
            const cap = tokenPx(isConversation ? "--width-conversation" : "--width-composer");
            const gutter = isConversation ? RESIZER_GUTTER_REM * remPx : 0;
            return {
              width: rect.width,
              parentWidth: parent.width,
              cap,
              expected: Math.min(cap, parent.width - gutter),
              scrollbarWidth: computed.getPropertyValue("scrollbar-width").trim(),
              center: Math.abs(rect.left + rect.right - (parent.left + parent.right)),
            };
          },
        );
        return {
          ratio: parseFloat(style.lineHeight) / parseFloat(style.fontSize),
          font: style.fontFamily,
          padding: parseFloat(style.paddingTop),
          columns,
          overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        };
      });
      expect(metrics.ratio).toBeCloseTo(1.6, 1);
      expect(metrics.font).not.toContain("monospace");
      expect(metrics.padding).toBeGreaterThanOrEqual(8);
      expect(metrics.columns).toHaveLength(2);

      // 消息流与输入区各按自己的上限收敛：消息流要装气泡，声明上限应当更宽。
      const [conversation, composer] = metrics.columns;
      expect(conversation.cap).toBeGreaterThan(composer.cap);

      for (const column of metrics.columns) {
        // 列宽 = min(声明上限, 容器可用宽度 - 手柄位)；容器更窄时占满可用宽度。
        expect(Math.abs(column.width - column.expected)).toBeLessThan(2);
        // 居中。
        expect(column.center).toBeLessThan(2);
      }
      // 消息流滚动时用细滚动条。
      expect(conversation.scrollbarWidth).toBe("thin");
      expect(metrics.overflow).toBeLessThanOrEqual(0);
      for (const dark of [false, true]) {
        await page.evaluate(
          (enabled) => document.documentElement.classList.toggle("dark", enabled),
          dark,
        );
        const colors = await input.evaluate((element) => {
          const probe = document.createElement("span");
          probe.style.color = "var(--color-text)";
          document.body.append(probe);
          const expected = getComputedStyle(probe).color;
          probe.remove();
          return { actual: getComputedStyle(element).color, expected };
        });
        expect(colors.actual).toBe(colors.expected);
      }
    });
  }
}

/** 列宽手柄：左右各一、落在滚动条外侧，拖动对称调宽，双击复位。 */
test("宽度手柄：左右各一且不压滚动条，拖拽对称调宽", async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 900 });
  await openProject(page);
  await page.locator(".room-list-row", { hasText: "architecture" }).click();
  await expect(page.locator(".chat-conversation")).toBeVisible();

  const geometry = await page.evaluate(() => {
    const conv = document.querySelector(".chat-conversation") as HTMLElement;
    const cr = conv.getBoundingClientRect();
    const handles = [...document.querySelectorAll(".conversation-resizer")] as HTMLElement[];
    return {
      conv: { left: cr.left, right: cr.right, top: cr.top, bottom: cr.bottom },
      handles: handles.map((handle) => {
        const rect = handle.getBoundingClientRect();
        return {
          side: handle.classList.contains("is-left") ? "left" : "right",
          left: rect.left,
          right: rect.right,
          top: rect.top,
          bottom: rect.bottom,
        };
      }),
    };
  });
  expect(geometry.handles.map((handle) => handle.side).sort()).toEqual(["left", "right"]);
  const [leftHandle, rightHandle] = geometry.handles;
  // 手柄整体落在列外侧：不压消息列，也就不压列内的滚动条。
  expect(leftHandle.right).toBeLessThanOrEqual(geometry.conv.left - 1);
  expect(rightHandle.left).toBeGreaterThanOrEqual(geometry.conv.right + 1);
  // 手柄纵向覆盖会话区，整列高度都能抓取。
  for (const handle of geometry.handles) {
    expect(handle.top).toBeLessThanOrEqual(geometry.conv.top + 2);
    expect(handle.bottom).toBeGreaterThanOrEqual(geometry.conv.bottom - 2);
  }

  const width = () =>
    page.evaluate(() =>
      Math.round(
        (document.querySelector(".chat-conversation") as HTMLElement).getBoundingClientRect().width,
      ),
    );
  const drag = async (selector: string, dx: number) => {
    const box = await page.locator(selector).boundingBox();
    if (!box) throw new Error(`missing handle: ${selector}`);
    const y = box.y + box.height / 2;
    await page.mouse.move(box.x + box.width / 2, y);
    await page.mouse.down();
    for (let step = 1; step <= 5; step += 1) {
      await page.mouse.move(box.x + box.width / 2 + (dx * step) / 5, y);
      await page.waitForTimeout(20);
    }
    await page.mouse.up();
    await page.waitForTimeout(80);
  };

  const start = await width();
  // 居中的列单侧拖动 20px，列宽变化 40px（两侧同时外扩）。
  await drag(".conversation-resizer.is-right", 20);
  expect(Math.abs((await width()) - (start + 40))).toBeLessThan(4);
  await drag(".conversation-resizer.is-left", -20);
  expect(Math.abs((await width()) - (start + 80))).toBeLessThan(4);
  // 调宽后手柄仍贴在列外侧，不会随着列变宽而被滚动条压住。
  const resized = await page.evaluate(() => {
    const conv = document.querySelector(".chat-conversation") as HTMLElement;
    const cr = conv.getBoundingClientRect();
    const handles = [...document.querySelectorAll(".conversation-resizer")] as HTMLElement[];
    return {
      conv: { left: cr.left, right: cr.right },
      handles: handles.map((handle) => {
        const rect = handle.getBoundingClientRect();
        return {
          side: handle.classList.contains("is-left") ? "left" : "right",
          left: rect.left,
          right: rect.right,
        };
      }),
    };
  });
  const resizedLeft = resized.handles.find((handle) => handle.side === "left")!;
  const resizedRight = resized.handles.find((handle) => handle.side === "right")!;
  expect(resizedLeft.right).toBeLessThanOrEqual(resized.conv.left - 1);
  expect(resizedRight.left).toBeGreaterThanOrEqual(resized.conv.right + 1);
  // 双击回到默认宽度。
  await page.locator(".conversation-resizer.is-right").dblclick();
  await page.waitForTimeout(100);
  expect(Math.abs((await width()) - start)).toBeLessThan(2);
});

/** 拖宽时锁定纵向阅读位置：上翻时锚定首条可见消息，位置由 scrollTop 主动补偿。 */
test("拖宽时锁定纵向阅读位置", async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 900 });
  await openProject(page);
  await page.locator(".room-list-row", { hasText: "architecture" }).click();
  const input = page.locator("textarea.field-multiline-chat");
  await expect(input).toBeVisible();

  // 演示房间内容不足以滚动：发几条长消息把会话撑到可纵向滚动。
  for (let i = 0; i < 8; i += 1) {
    await input.fill(
      `消息 ${i}：` +
        Array(30)
          .fill("这是一段足够长的聊天内容，用来把会话撑到可以纵向滚动，观察拖宽时的滚动位置。")
          .join(" "),
    );
    await input.press("Enter");
    await page.waitForTimeout(200);
  }
  await page.waitForTimeout(400);

  const readState = () =>
    page.evaluate(() => {
      const el = document.querySelector(".chat-conversation") as HTMLElement;
      const containerTop = el.getBoundingClientRect().top;
      const entries = [...el.querySelectorAll(".chat-entry")] as HTMLElement[];
      const firstVisible = entries.find(
        (entry) => entry.getBoundingClientRect().bottom > containerTop,
      );
      return {
        scrollTop: el.scrollTop,
        max: el.scrollHeight - el.clientHeight,
        anchor: firstVisible ? firstVisible.getBoundingClientRect().top - containerTop : null,
      };
    });

  // 放到中间位置：既非贴底，锚点之上也有内容，拖窄必然引起回流。
  await page.evaluate(() => {
    const el = document.querySelector(".chat-conversation") as HTMLElement;
    el.scrollTop = Math.round((el.scrollHeight - el.clientHeight) / 2);
  });
  await page.waitForTimeout(120);
  const before = await readState();
  expect(before.anchor).not.toBeNull();

  const box = await page.locator(".conversation-resizer.is-right").boundingBox();
  if (!box) throw new Error("missing right handle");
  const y = box.y + box.height / 2;
  await page.mouse.move(box.x + box.width / 2, y);
  await page.mouse.down();
  for (let step = 1; step <= 6; step += 1) {
    await page.mouse.move(box.x + box.width / 2 - (36 * step) / 6, y);
    await page.waitForTimeout(30);
  }
  await page.mouse.up();
  await page.waitForTimeout(120);
  const after = await readState();

  // 首条可见消息停在原视口偏移；scrollTop 主动补偿，而不是内容被卷走。
  expect(Math.abs((after.anchor ?? 0) - (before.anchor ?? 0))).toBeLessThan(2);
  expect(after.scrollTop).toBeGreaterThan(before.scrollTop + 10);
  expect(after.max).toBeGreaterThan(before.max + 10);

  // 贴底时调宽保持贴底：内容高度随宽度变化，视口仍然停在最新消息处。
  await page.evaluate(() => {
    const el = document.querySelector(".chat-conversation") as HTMLElement;
    el.scrollTop = el.scrollHeight;
  });
  await page.waitForTimeout(120);
  const bottomBefore = await readState();
  expect(bottomBefore.max - bottomBefore.scrollTop).toBeLessThan(2);

  const bottomBox = await page.locator(".conversation-resizer.is-right").boundingBox();
  if (!bottomBox) throw new Error("missing right handle");
  const bottomY = bottomBox.y + bottomBox.height / 2;
  await page.mouse.move(bottomBox.x + bottomBox.width / 2, bottomY);
  await page.mouse.down();
  for (let step = 1; step <= 4; step += 1) {
    await page.mouse.move(bottomBox.x + bottomBox.width / 2 - (24 * step) / 4, bottomY);
    await page.waitForTimeout(30);
  }
  await page.mouse.up();
  await page.waitForTimeout(120);
  const bottomAfter = await readState();
  expect(bottomAfter.max - bottomAfter.scrollTop).toBeLessThan(2);
});
