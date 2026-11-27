import { expect, test, type WebSocketRoute } from "@playwright/test";
import { ObserverClient } from "@ghrah/observer-core";
import { AgentSnapshotSchema, RoomInfoPayloadSchema } from "@ghrah/protocol";

// Agent 由模板创建：模板身份来自 Subject 模板库，mock 内置一份同 id 的模板。
const MOCK_TEMPLATE_ID = "mock.template";
const MOCK_TEMPLATE_REVISION = "1";

test("Room reads transcript and reconciles identical sends without the WS history command", async ({
  page,
}) => {
  const socketUrl = "ws://127.0.0.1:4113/ws";
  const client = new ObserverClient(socketUrl);
  await client.connect();
  const projectName = `transcript-${crypto.randomUUID()}`;
  const result = await client.createProject(projectName, {
    projectRootLocator: "/mock/transcript",
  });
  const projectId = (result.data as { project: { project_id: string } }).project.project_id;
  try {
    const agent = AgentSnapshotSchema.parse(
      (
        await client.createAgentEntity({
          project_id: projectId,
          name: "transcript-worker",
          template_id: MOCK_TEMPLATE_ID,
          revision: 1,
          start: true,
        })
      ).data,
    );
    const agentId = agent.identity.agent_id;
    const created = await client.createRoom(projectId, "Transcript Room");
    const room = RoomInfoPayloadSchema.parse((created.data as { room: unknown }).room);
    expect(
      (await client.joinRoom(room.room_id, agentId, "agent", "transcript-worker")).success,
    ).toBe(true);
    expect(
      (
        await client.roomSend(
          room.room_id,
          { message: "Archived final reply", ability: "end_task" },
          agentId,
          "agent",
        )
      ).success,
    ).toBe(true);

    let browserSocket: WebSocketRoute | undefined;
    let beforeReconnect: (() => Promise<void>) | undefined;
    const wsRoomSends: string[] = [];
    const wsHistoryCommands: string[] = [];
    await page.routeWebSocket(
      (url) => `${url.origin}${url.pathname}` === socketUrl,
      async (socket) => {
        const prepare = beforeReconnect;
        beforeReconnect = undefined;
        await prepare?.();
        const server = socket.connectToServer();
        browserSocket = socket;
        socket.onMessage((payload) => {
          if (payload.toString().includes('"type":"room_send"'))
            wsRoomSends.push(payload.toString());
          if (payload.toString().includes('"type":"room_get_log"'))
            wsHistoryCommands.push(payload.toString());
          server.send(payload);
        });
      },
    );
    await page.addInitScript(() => localStorage.setItem("ghrah-locale", "en"));
    await page.goto("/");
    await page.locator(".project-item", { hasText: projectName }).click();
    const endpoint = `/rooms/${room.room_id}/transcript`;
    const tail = page.waitForResponse((response) =>
      new URL(response.url()).pathname.endsWith(endpoint),
    );
    await page.locator(".room-list-row", { hasText: room.name }).press("Enter");
    expect((await tail).status()).toBe(200);
    await expect(page.locator("[data-ability=end_task]")).toHaveText(/Archived final reply/);

    const composer = page.locator(".chat-composer");
    for (let count = 0; count < 2; count += 1) {
      await composer.locator("textarea").fill("same text twice");
      await composer.locator('button[type="submit"]').click();
    }
    await expect(
      page.locator("[class*=entry-human--]", { hasText: "same text twice" }),
    ).toHaveCount(2);
    await expect(page.locator("[data-pending=true]")).toHaveCount(0);
    expect(wsRoomSends).toHaveLength(2);

    await page.locator('button[aria-controls="room-detail-drawer"]').click();
    const manual = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return url.pathname.endsWith(endpoint) && url.searchParams.has("since_seq");
    });
    await page
      .locator("#room-detail-drawer")
      .getByRole("button", { name: "Refresh delivery status", exact: true })
      .click();
    expect((await manual).status()).toBe(200);

    beforeReconnect = async () => {
      const sent = await client.roomSend(
        room.room_id,
        { message: "Reply while disconnected" },
        agentId,
        "agent",
      );
      expect(sent.success).toBe(true);
    };
    const reloadedTail = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return (
        url.pathname.endsWith(endpoint) &&
        url.searchParams.get("limit") === "50" &&
        !url.searchParams.has("before_seq") &&
        !url.searchParams.has("since_seq")
      );
    });
    expect(browserSocket).toBeDefined();
    await browserSocket!.close({ code: 1001, reason: "reconnect test" });
    expect((await reloadedTail).status()).toBe(200);
    await expect(page.getByText("Reply while disconnected", { exact: true })).toBeVisible();
    await expect(
      page.locator("[class*=entry-human--]", { hasText: "same text twice" }),
    ).toHaveCount(2);
    expect(wsHistoryCommands).toEqual([]);
  } finally {
    const current = await client.getProject(projectId);
    const project = (current.data as { project: { version: number } }).project;
    await client.deleteProject({ projectId, expectedVersion: project.version, cascadeRooms: true });
    await client.disconnect();
  }
});

test("mock: create entity, start, create Session, send message and read offline history", async ({
  page,
}) => {
  const client = new ObserverClient("ws://127.0.0.1:4113/ws");
  await client.connect();
  const projectName = `browser-flow-${crypto.randomUUID()}`;
  const result = await client.createProject(projectName, { projectRootLocator: "/mock/browser" });
  const projectId = (result.data as { project: { project_id: string } }).project.project_id;
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  try {
    await page.addInitScript(() => localStorage.setItem("ghrah-locale", "en"));
    await page.goto("/");
    await page.locator(".project-item", { hasText: projectName }).click();
    await page.locator(".project-fixed-nav .sidebar-row", { hasText: "Agent overview" }).click();
    await page.getByRole("button", { name: "Create agent entity", exact: true }).click();
    const createDialog = page.getByRole("dialog", { name: "Create agent entity", exact: true });
    await createDialog.getByLabel("Name", { exact: true }).fill("browser-worker");
    await createDialog.getByLabel("Agent template", { exact: true }).fill(MOCK_TEMPLATE_ID);
    await createDialog
      .getByLabel("Template revision", { exact: true })
      .fill(MOCK_TEMPLATE_REVISION);
    await createDialog.getByRole("button", { name: "Create agent entity", exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "Agent entity · browser-worker", exact: true });
    await expect(dialog.locator(".entity-facts")).toContainText("Offline");
    await dialog.getByRole("button", { name: "Start agent", exact: true }).click();
    await expect(dialog.locator(".entity-facts")).toContainText("Online");
    await dialog.getByRole("button", { name: "Sessions & history", exact: true }).click();
    await dialog.getByLabel("New session name", { exact: true }).fill("browser-session");
    await dialog
      .getByLabel("Session system prompt (optional)", { exact: true })
      .fill("Browser prompt");
    await dialog.getByRole("button", { name: "Create session", exact: true }).click();
    await expect(dialog.getByRole("combobox", { name: "Session", exact: true })).toContainText(
      "browser-session",
    );
    const entities = await client.listAgentEntities(projectId);
    const agentId = (entities.data as { agents: Array<{ identity: { agent_id: string } }> })
      .agents[0].identity.agent_id;
    const sent = await client.sendMessage(
      { projectId, agentId, agentName: "browser-worker" },
      "browser hello",
    );
    expect(sent.success).toBe(true);
    await dialog.getByRole("button", { name: "Messages", exact: true }).click();
    await expect(dialog).toContainText("browser hello");
    await expect(dialog).toContainText("Completed");
    await dialog.getByRole("button", { name: "Overview", exact: true }).click();
    await dialog.getByRole("button", { name: "Stop agent", exact: true }).click();
    await dialog.getByRole("button", { name: "Confirm operation", exact: true }).click();
    await expect(dialog.locator(".entity-facts")).toContainText("Offline");
    await dialog.getByRole("button", { name: "Sessions & history", exact: true }).click();
    await dialog.getByRole("button", { name: "Load history", exact: true }).click();
    await expect(dialog).toContainText("browser hello");
    await dialog.getByRole("button", { name: "Close", exact: true }).click();
    const snapshot = await client.agent(projectId, agentId).start();
    expect(snapshot.runtime.availability).toBe("online");
    const peerResult = await client.createAgentEntity({
      project_id: projectId,
      name: "browser-peer",
      template_id: MOCK_TEMPLATE_ID,
      revision: 1,
      start: true,
    });
    const peer = AgentSnapshotSchema.parse(peerResult.data);
    const communication = page.locator(".communication-panel");
    await communication.locator("summary").click();
    await communication.locator(".communication-link select").nth(0).selectOption(agentId);
    await communication
      .locator(".communication-link select")
      .nth(1)
      .selectOption(peer.identity.agent_id);
    await communication.locator("textarea").fill("browser A to B");
    await communication.getByRole("button", { name: "Run verification", exact: true }).click();
    await expect(communication).toContainText("[mock:browser-peer] browser A to B");
    await page
      .locator(".agents-overview-list li", { hasText: "browser-worker" })
      .getByRole("button", { name: "Open assembly", exact: true })
      .click();
    const assembly = page.locator(".assembly-detail");
    await expect(assembly).toContainText("Lifecycle: running");
    await assembly.getByRole("tab", { name: "Grants", exact: true }).click();
    await assembly.getByRole("checkbox", { name: "Available", exact: true }).uncheck();
    const savedGrant = page.waitForResponse(
      (response) =>
        response.request().method() === "PUT" &&
        response.url().endsWith("/ability-tree/grants/ghrah-fs"),
    );
    await assembly.getByRole("button", { name: "Save grant", exact: true }).click();
    expect((await savedGrant).status()).toBe(200);
    await assembly.getByRole("tab", { name: "Tools", exact: true }).click();
    const selectedTool = assembly.locator(".tool-selector input");
    const unbound = page.waitForResponse(
      (response) =>
        response.request().method() === "DELETE" &&
        response.url().includes("/ability-tree/tools/read_file"),
    );
    await selectedTool.uncheck();
    expect((await unbound).status()).toBe(200);
    const bound = page.waitForResponse(
      (response) =>
        response.request().method() === "PUT" &&
        response.url().endsWith("/ability-tree/tools/read_file"),
    );
    await selectedTool.check();
    expect((await bound).status()).toBe(200);
    await expect(assembly.locator(".respawn-banner")).toContainText(
      "These changes take effect after this agent restarts.",
    );
    await client.agent(projectId, agentId).stop();
    const afterRestart = await client.agent(projectId, agentId).start();
    await assembly.getByRole("tab", { name: "Overview", exact: true }).click();
    await expect(assembly).toContainText(afterRestart.runtime.incarnation_id!);
    await expect(assembly.locator(".respawn-banner")).not.toContainText(
      "These changes take effect after this agent restarts.",
    );
    await assembly.getByRole("tab", { name: "Changes", exact: true }).click();
    await expect(assembly).toContainText("Reason bind_tool");
    await assembly.getByRole("tab", { name: "Versions", exact: true }).click();
    await expect(assembly).toContainText("Filesystem");
    expect(pageErrors).toEqual([]);
  } finally {
    const current = await client.getProject(projectId);
    const project = (current.data as { project: { version: number } }).project;
    await client.deleteProject({ projectId, expectedVersion: project.version, cascadeRooms: true });
    await client.disconnect();
  }
});
