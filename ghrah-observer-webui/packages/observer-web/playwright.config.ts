import { defineConfig, devices } from "@playwright/test";

// 视觉回归的固定端口：4113 是 mock-server 的默认演示端口（避开真实 server 的 4112），
// 5180 专供 e2e，避免与日常 dev 的 5173 冲突。
const MOCK_PORT = 4113;
const APP_PORT = 5180;

export default defineConfig({
  testDir: "./e2e",
  // 所有用例共享同一个 mock-server 进程与同一份演示数据，串行执行最稳定。
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: 0,
  timeout: 45_000,
  reporter: process.env.CI ? [["github"], ["list"]] : [["list"]],
  expect: {
    timeout: 15_000,
    toHaveScreenshot: {
      // 同一台机器上应逐像素一致；留极小容差给字体抗锯齿。
      maxDiffPixelRatio: 0.002,
      // 关掉动画与光标闪烁，避免截图相位差。
      animations: "disabled",
      caret: "hide",
      scale: "css",
    },
  },
  use: {
    baseURL: `http://127.0.0.1:${APP_PORT}`,
    viewport: { width: 1600, height: 900 },
    deviceScaleFactor: 1,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1600, height: 900 } },
    },
  ],
  webServer: [
    {
      // e2e/mock-server.mjs 固定了场景参数：replay-on-connect 让“晚连”的页面也能拿到
      // 完整演示状态；--no-chains 关掉随时间追加的 ActionChain 事件；hitl-timeout 调大，
      // 避免待审批项 30 秒后自动消失。ws 服务对 GET 返回 426，健康端点由该包装脚本提供。
      command: "node e2e/mock-server.mjs",
      url: "http://127.0.0.1:4114/health",
      cwd: import.meta.dirname,
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      // 用生产构建 + preview，保证截图对应真实产物；WS 地址在构建时注入。
      command: `VITE_GHRAH_SUBJECT_WS_URL=ws://127.0.0.1:${MOCK_PORT}/ws ./node_modules/.bin/vite build --logLevel warn && ./node_modules/.bin/vite preview --port ${APP_PORT} --strictPort --host 127.0.0.1`,
      url: `http://127.0.0.1:${APP_PORT}`,
      cwd: import.meta.dirname,
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
