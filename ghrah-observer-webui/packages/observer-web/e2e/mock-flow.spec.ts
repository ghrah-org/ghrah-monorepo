import { expect, test } from "@playwright/test";
import { ObserverClient } from "@ghrah/observer-core";
import { AgentSnapshotSchema } from "@ghrah/protocol";

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
    await createDialog.getByLabel("Agent manifest reference", { exact: true }).fill("mock.agent");
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
      manifest_ref: "mock.agent",
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
