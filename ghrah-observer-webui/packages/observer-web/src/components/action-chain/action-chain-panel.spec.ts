// @vitest-environment happy-dom

import {
  type AgentTarget,
  type ChainTarget,
  type SessionTarget,
  useActionChainsStore,
  useAgentsStore,
  useBranchesStore,
  useSessionsStore,
} from "@ghrah/observer-core";
import {
  type ActionNode,
  ActionNodeSchema,
  type AgentSpawnedPayload,
  BranchInfoPayloadSchema,
  SessionInfoPayloadSchema,
} from "@ghrah/protocol";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

const activateSession = vi.fn();
const activateBranch = vi.fn();
const createSession = vi.fn();
const createBranch = vi.fn();
const archiveSession = vi.fn();
const deleteSession = vi.fn();
const archiveBranch = vi.fn();
const deleteBranch = vi.fn();
const agentReset = vi.fn();

vi.mock("@/composables/useObserver", () => ({
  useObserver: () => ({
    activateSession,
    activateBranch,
    createSession,
    createBranch,
    archiveSession,
    deleteSession,
    archiveBranch,
    deleteBranch,
    agentReset,
  }),
}));

import ActionChainPanel from "./action-chain-panel.vue";
import CreateSessionDialog from "../create-session-dialog.vue";

/** 从画布节点 DOM 提取 meta 行文本（节点去重断言用）。 */
function textOfMeta(
  nodeWrapper: ReturnType<import("@vue/test-utils").VueWrapper["findAll"]>[number],
): string {
  const meta = nodeWrapper.find(".ac-node-meta");
  return meta.exists() ? meta.text() : nodeWrapper.text();
}

function node(overrides: Partial<ActionNode> = {}): ActionNode {
  return ActionNodeSchema.parse(overrides);
}

function chainNodes(name: string, overrides: Partial<ActionNode>[]): ActionNode[] {
  return overrides.map((item) =>
    node({ ...item, session_id: `${name}-session`, created_on_branch_id: `${name}-branch` }),
  );
}

function target(name: string): AgentTarget {
  return { projectId: "p1", agentId: `${name}-id`, agentName: name };
}

function spawn(name: string): AgentSpawnedPayload {
  return {
    type: "agent_spawned",
    project_id: "p1",
    agent_id: `${name}-id`,
    cluster_id: "cluster",
    incarnation_id: "incarnation",
    recovery_mode: "fresh",
    name,
    config: { name, agent_id: `${name}-id` },
  } as unknown as AgentSpawnedPayload;
}

function chainTarget(name: string): ChainTarget {
  return { ...target(name), sessionId: `${name}-session`, branchId: `${name}-branch` };
}

/** 画布视口横向夹具（happy-dom 无布局）：scrollWidth 可变，scrollLeft 赋值带钳制。 */
function mockViewportScroll(
  el: HTMLElement,
  metrics: { scrollWidth: number; clientWidth: number },
): void {
  let left = 0;
  Object.defineProperty(el, "scrollWidth", { configurable: true, get: () => metrics.scrollWidth });
  Object.defineProperty(el, "clientWidth", { configurable: true, get: () => metrics.clientWidth });
  Object.defineProperty(el, "scrollLeft", {
    configurable: true,
    get: () => left,
    set: (v: number) => {
      left = Math.max(0, Math.min(v, metrics.scrollWidth - metrics.clientWidth));
    },
  });
}

function setActiveChain(name: string, overrides: Partial<ActionNode>[]) {
  const nodes = chainNodes(name, overrides);
  const agent = target(name);
  const sessionId = `${name}-session`;
  const session: SessionTarget = { ...agent, sessionId };
  // head = 不作为任何节点 parent 的节点中的最后一个（测试夹具为单链/单 Head）
  const parentIds = new Set(nodes.map((item) => item.parent_id).filter(Boolean));
  const heads = nodes.filter((item) => item.id && !parentIds.has(item.id));
  const head = heads.at(-1)?.id ?? nodes.at(-1)?.id ?? "root";
  useSessionsStore().replaceAgentSessions(agent, [
    SessionInfoPayloadSchema.parse({
      session_id: sessionId,
      agent_name: name,
      root_node_id: nodes[0]?.id ?? "root",
      active_branch_id: `${name}-branch`,
    }),
  ]);
  useSessionsStore().setActiveSession(agent, sessionId);
  useBranchesStore().replaceSessionBranches(
    session,
    [
      BranchInfoPayloadSchema.parse({
        branch_id: `${name}-branch`,
        session_id: sessionId,
        name: "main",
        head_node_id: head,
      }),
    ],
    { explicitActiveBranchId: `${name}-branch` },
  );
  useActionChainsStore().setChain(chainTarget(name), nodes);
}

describe("ActionChainPanel", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    for (const mock of [
      activateSession,
      activateBranch,
      createSession,
      createBranch,
      archiveSession,
      deleteSession,
      archiveBranch,
      deleteBranch,
      agentReset,
    ]) {
      mock.mockReset();
      mock.mockResolvedValue({ success: true });
    }
  });

  it("shows empty state when the target agent has no chain", async () => {
    const agents = useAgentsStore();
    agents.onAgentSpawned(spawn("alpha"));
    const wrapper = mount(ActionChainPanel, { props: { agent: target("alpha") } });
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain("No actions yet");
  });

  it("renders only the target agent's chain", async () => {
    const agents = useAgentsStore();
    agents.onAgentSpawned(spawn("alpha"));
    agents.onAgentSpawned(spawn("beta"));
    setActiveChain("alpha", [{ id: "a1", parent_id: null, timestamp: "t1" }]);
    setActiveChain("beta", [{ id: "b1", parent_id: null, timestamp: "t1" }]);
    const wrapper = mount(ActionChainPanel, { props: { agent: target("alpha") } });
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain("@alpha");
    expect(wrapper.text()).not.toContain("@beta");
    expect(wrapper.findAll(".ac-node")).toHaveLength(1);
  });

  it("switches canvas when the agent prop changes", async () => {
    const agents = useAgentsStore();
    agents.onAgentSpawned(spawn("alpha"));
    agents.onAgentSpawned(spawn("beta"));
    setActiveChain("alpha", [{ id: "a1", parent_id: null, timestamp: "t1" }]);
    setActiveChain("beta", [
      { id: "b1", parent_id: null, timestamp: "t1" },
      { id: "b2", parent_id: "b1", timestamp: "t2" },
    ]);
    const wrapper = mount(ActionChainPanel, { props: { agent: target("alpha") } });
    await wrapper.vm.$nextTick();
    expect(wrapper.findAll(".ac-node")).toHaveLength(1);
    await wrapper.setProps({ agent: target("beta") });
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain("@beta");
    expect(wrapper.text()).not.toContain("@alpha");
    expect(wrapper.findAll(".ac-node")).toHaveLength(2);
  });

  it("renders parent_id tree with indent guides", async () => {
    const agents = useAgentsStore();
    const sessions = useSessionsStore();
    const branches = useBranchesStore();
    const chainsStore = useActionChainsStore();
    agents.onAgentSpawned(spawn("alpha"));
    const agent = target("alpha");
    const session: SessionTarget = { ...agent, sessionId: "alpha-session" };
    sessions.replaceAgentSessions(agent, [
      SessionInfoPayloadSchema.parse({
        session_id: "alpha-session",
        agent_name: "alpha",
        root_node_id: "root",
        active_branch_id: "main",
      }),
    ]);
    sessions.setActiveSession(agent, "alpha-session");
    branches.replaceSessionBranches(
      session,
      [
        BranchInfoPayloadSchema.parse({
          branch_id: "main",
          session_id: "alpha-session",
          name: "main",
          head_node_id: "c1a",
        }),
        BranchInfoPayloadSchema.parse({
          branch_id: "retry",
          session_id: "alpha-session",
          name: "retry",
          head_node_id: "c2",
        }),
      ],
      { explicitActiveBranchId: "main" },
    );
    chainsStore.setChain({ ...session, branchId: "main" }, [
      node({
        id: "root",
        parent_id: null,
        timestamp: "t0",
        session_id: "alpha-session",
        created_on_branch_id: "main",
      }),
      node({
        id: "c1",
        parent_id: "root",
        timestamp: "t1",
        session_id: "alpha-session",
        created_on_branch_id: "main",
      }),
      node({
        id: "c1a",
        parent_id: "c1",
        timestamp: "t1a",
        session_id: "alpha-session",
        created_on_branch_id: "main",
      }),
    ]);
    chainsStore.setChain({ ...session, branchId: "retry" }, [
      node({
        id: "c2",
        parent_id: "root",
        timestamp: "t2",
        session_id: "alpha-session",
        created_on_branch_id: "retry",
      }),
    ]);

    const wrapper = mount(ActionChainPanel, { props: { agent: target("alpha") } });
    await wrapper.vm.$nextTick();
    const branchSelect = wrapper.get<HTMLSelectElement>(".chain-branch-select");
    await branchSelect.setValue("__all__");
    await wrapper.vm.$nextTick();
    expect(wrapper.findAll(".ac-node")).toHaveLength(4);
    // 单 Session 泳道布局：main(3 节点) 泳道 0 + retry(1 节点) 泳道 1 → 1 条泳道分隔线
    expect(wrapper.findAll(".ac-lane-guide")).toHaveLength(1);
  });

  it("classifies conversation nodes as converse on the canvas", async () => {
    const agents = useAgentsStore();
    agents.onAgentSpawned(spawn("alpha"));
    setActiveChain("alpha", [
      {
        id: "n1",
        parent_id: null,
        timestamp: "t1",
        ability_names: ["conversation"],
        messages_delta: [
          {
            role: "ai",
            content_blocks: [
              { type: "text", text: "hello reply" },
              { type: "tool_call", id: "x", name: "send_message", arguments: {} },
              { type: "reasoning", reasoning: "think", incomplete: false },
            ],
            metadata: {},
          },
        ],
      },
    ]);
    const wrapper = mount(ActionChainPanel, { props: { agent: target("alpha") } });
    await wrapper.vm.$nextTick();
    expect(wrapper.findAll(".ac-node--converse")).toHaveLength(1);
    // 详情区（尽力渲染）后置阶段 3：卡片摘要不含消息内容
    expect(wrapper.text()).not.toContain("hello reply");
  });

  it("plain select only views; explicit buttons activate runtime", async () => {
    const agents = useAgentsStore();
    const sessions = useSessionsStore();
    const branches = useBranchesStore();
    agents.onAgentSpawned(spawn("alpha"));

    const agent = target("alpha");
    const active: SessionTarget = { ...agent, sessionId: "alpha-session-2" };
    sessions.replaceAgentSessions(agent, [
      SessionInfoPayloadSchema.parse({
        session_id: "alpha-session",
        agent_name: "alpha",
        root_node_id: "root",
        active_branch_id: "alpha-branch",
      }),
      SessionInfoPayloadSchema.parse({
        session_id: "alpha-session-2",
        agent_name: "alpha",
        root_node_id: "root-2",
        active_branch_id: "alpha-branch-2",
      }),
    ]);
    sessions.setActiveSession(agent, "alpha-session-2");
    branches.replaceSessionBranches(active, [
      BranchInfoPayloadSchema.parse({
        branch_id: "alpha-branch-2",
        session_id: "alpha-session-2",
        name: "Branch 2",
        head_node_id: "root-2",
      }),
    ]);

    const wrapper = mount(ActionChainPanel, { props: { agent: target("alpha") } });
    await wrapper.vm.$nextTick();
    const sessionSelect = wrapper.get<HTMLSelectElement>(".chain-session-select");
    // viewed 初始回退 runtime active Session
    expect(sessionSelect.element.value).toBe("alpha-session-2");

    // 普通切换 Session = 纯查看，不发 activate 命令
    await sessionSelect.setValue("alpha-session");
    await wrapper.vm.$nextTick();
    expect(activateSession).not.toHaveBeenCalled();
    expect(sessions.viewedSessionId(agent)).toBe("alpha-session");
    // runtime active 不被查看选择抢占
    expect(sessions.activeSessionId(agent)).toBe("alpha-session-2");

    // 显式激活按钮出现并发命令
    const activateBtn = wrapper.get("button.chain-activate-session");
    vi.spyOn(window, "confirm").mockReturnValue(true);
    await activateBtn.trigger("click");
    expect(activateSession).toHaveBeenCalledWith({ ...agent, sessionId: "alpha-session" });

    // Branch select 同理：普通切换只查看
    const branchSelect = wrapper.get<HTMLSelectElement>(".chain-branch-select");
    await branchSelect.setValue("alpha-branch-2");
    expect(activateBranch).not.toHaveBeenCalled();
  });

  it("activates branch explicitly from viewed branch", async () => {
    const agents = useAgentsStore();
    const sessions = useSessionsStore();
    const branches = useBranchesStore();
    agents.onAgentSpawned(spawn("alpha"));
    const agent = target("alpha");
    const session: SessionTarget = { ...agent, sessionId: "alpha-session" };
    sessions.replaceAgentSessions(agent, [
      SessionInfoPayloadSchema.parse({
        session_id: "alpha-session",
        agent_name: "alpha",
        root_node_id: "root",
        active_branch_id: "alpha-branch",
      }),
    ]);
    sessions.setActiveSession(agent, "alpha-session");
    branches.replaceSessionBranches(session, [
      BranchInfoPayloadSchema.parse({
        branch_id: "alpha-branch",
        session_id: "alpha-session",
        name: "Branch 1",
        head_node_id: "root",
      }),
      BranchInfoPayloadSchema.parse({
        branch_id: "alpha-branch-2",
        session_id: "alpha-session",
        name: "Branch 2",
        head_node_id: "root",
      }),
    ]);

    const wrapper = mount(ActionChainPanel, { props: { agent: target("alpha") } });
    await wrapper.vm.$nextTick();
    const branchSelect = wrapper.get<HTMLSelectElement>(".chain-branch-select");
    await branchSelect.setValue("alpha-branch-2");
    expect(activateBranch).not.toHaveBeenCalled();

    vi.spyOn(window, "confirm").mockReturnValue(true);
    const btn = wrapper.get("button.chain-activate-branch");
    await btn.trigger("click");
    expect(activateBranch).toHaveBeenCalledWith({
      ...agent,
      sessionId: "alpha-session",
      branchId: "alpha-branch-2",
    });
  });

  it("creates a session; view follows the receipt without runtime activation", async () => {
    const agents = useAgentsStore();
    const sessions = useSessionsStore();
    agents.onAgentSpawned(spawn("alpha"));
    const wrapper = mount(ActionChainPanel, {
      props: { agent: target("alpha") },
      global: { stubs: { CreateSessionDialog: true } },
    });
    await wrapper.get("button.chain-new-session").trigger("click");
    const dialog = wrapper.getComponent(CreateSessionDialog);
    expect(dialog.props()).toMatchObject({ agent: target("alpha"), route: "chain" });
    expect(createSession).not.toHaveBeenCalled();
    dialog.vm.$emit("created", "alpha-session-2");
    // D6：新建只切展示，不隐式激活运行态
    expect(activateSession).not.toHaveBeenCalled();
    expect(sessions.viewedSessionId(target("alpha"))).toBe("alpha-session-2");
  });

  it("creates a branch; view follows the receipt, surfaces failures", async () => {
    const agents = useAgentsStore();
    const sessions = useSessionsStore();
    const branches = useBranchesStore();
    agents.onAgentSpawned(spawn("alpha"));
    const agent = target("alpha");
    const session: SessionTarget = { ...agent, sessionId: "alpha-session" };
    sessions.replaceAgentSessions(agent, [
      SessionInfoPayloadSchema.parse({
        session_id: "alpha-session",
        agent_name: "alpha",
        root_node_id: "root",
        active_branch_id: "alpha-branch",
      }),
    ]);
    sessions.setActiveSession(agent, "alpha-session");
    branches.replaceSessionBranches(session, [
      BranchInfoPayloadSchema.parse({
        branch_id: "alpha-branch",
        session_id: "alpha-session",
        name: "Branch 1",
        head_node_id: "root",
      }),
    ]);

    createBranch.mockResolvedValue({ success: true, data: { branch_id: "alpha-branch-2" } });
    const wrapper = mount(ActionChainPanel, { props: { agent: target("alpha") } });
    await wrapper.vm.$nextTick();
    await wrapper.get("button.chain-new-branch").trigger("click");
    expect(createBranch).toHaveBeenCalledWith(session, "Branch 2");
    // D6：新建 Branch 不隐式激活
    expect(activateBranch).not.toHaveBeenCalled();
    expect(branches.viewedBranchId(session)).toBe("alpha-branch-2");

    createBranch.mockResolvedValue({
      success: false,
      error: "fork node not found",
      error_detail: "fork node is not readable",
    });
    await wrapper.get("button.chain-new-branch").trigger("click");
    await wrapper.vm.$nextTick();
    expect(wrapper.get('[role="alert"]').text()).toContain("fork node is not readable");
  });

  it("renders all branches merged when viewing all-branches mode", async () => {
    const agents = useAgentsStore();
    const sessions = useSessionsStore();
    const branches = useBranchesStore();
    const chainsStore = useActionChainsStore();
    agents.onAgentSpawned(spawn("alpha"));
    const agent = target("alpha");
    const session: SessionTarget = { ...agent, sessionId: "alpha-session" };
    sessions.replaceAgentSessions(agent, [
      SessionInfoPayloadSchema.parse({
        session_id: "alpha-session",
        agent_name: "alpha",
        root_node_id: "root",
        active_branch_id: "main",
      }),
    ]);
    sessions.setActiveSession(agent, "alpha-session");
    branches.replaceSessionBranches(session, [
      BranchInfoPayloadSchema.parse({
        branch_id: "main",
        session_id: "alpha-session",
        name: "main",
        head_node_id: "n2",
      }),
      BranchInfoPayloadSchema.parse({
        branch_id: "retry",
        session_id: "alpha-session",
        name: "retry-1",
        head_node_id: "r2",
      }),
    ]);
    // 共享祖先 root；main: root→n1→n2；retry: root→r1→r2
    chainsStore.setChain({ ...session, branchId: "main" }, [
      node({
        id: "root",
        parent_id: null,
        timestamp: "t0",
        iteration: 0,
        session_id: "alpha-session",
        created_on_branch_id: "main",
      }),
      node({
        id: "n1",
        parent_id: "root",
        timestamp: "t1",
        iteration: 1,
        session_id: "alpha-session",
        created_on_branch_id: "main",
      }),
      node({
        id: "n2",
        parent_id: "n1",
        timestamp: "t2",
        iteration: 2,
        session_id: "alpha-session",
        created_on_branch_id: "main",
      }),
    ]);
    chainsStore.setChain({ ...session, branchId: "retry" }, [
      node({
        id: "r1",
        parent_id: "root",
        timestamp: "t3",
        iteration: 3,
        session_id: "alpha-session",
        created_on_branch_id: "retry",
      }),
      node({
        id: "r2",
        parent_id: "r1",
        timestamp: "t4",
        iteration: 4,
        session_id: "alpha-session",
        created_on_branch_id: "retry",
      }),
    ]);
    // root 在 main 桶；retry 桶缺 root，回溯到 root 停止（缺父保留已知部分）

    const wrapper = mount(ActionChainPanel, { props: { agent: target("alpha") } });
    await wrapper.vm.$nextTick();
    const branchSelect = wrapper.get<HTMLSelectElement>(".chain-branch-select");
    await branchSelect.setValue("__all__");
    await wrapper.vm.$nextTick();
    // 全部 Branch 模式：main(n2)+retry(r2) 两 Head 回溯合并去重（root/n1 共享）= 5
    expect(wrapper.findAll(".ac-node")).toHaveLength(5);
    // 合并布局去重：共享祖先 root 只渲染一次
    const summaryTexts = wrapper.findAll(".ac-node").map((n) => textOfMeta(n));
    const unique = new Set(summaryTexts);
    expect(unique.size).toBe(summaryTexts.length);
  });

  it("selects and clears a node via canvas interactions", async () => {
    const agents = useAgentsStore();
    agents.onAgentSpawned(spawn("alpha"));
    setActiveChain("alpha", [
      { id: "a1", parent_id: null, timestamp: "t1" },
      { id: "a2", parent_id: "a1", timestamp: "t2" },
    ]);
    const wrapper = mount(ActionChainPanel, { props: { agent: target("alpha") } });
    await wrapper.vm.$nextTick();
    // 横向滚动出口条件：画布视口为唯一滚动容器，AgentName 头与图例在滚动区之外
    const viewport = wrapper.find(".ac-canvas-viewport");
    expect(viewport.exists()).toBe(true);
    const header = wrapper.find(".chain-header, .chain-canvas-scroll");
    expect(header.exists()).toBe(false);
    // AgentName 头部不得在滚动视口内
    const headerText = wrapper.findAll("div").filter((d) => d.text() === "@alpha");
    expect(headerText.length).toBeGreaterThan(0);
    expect(headerText[0].element.parentElement?.closest(".ac-canvas-viewport")).toBeNull();
    const nodes = wrapper.findAll(".ac-node");
    await nodes[1].trigger("click");
    await wrapper.vm.$nextTick();
    expect(wrapper.findAll(".ac-node.selected")).toHaveLength(1);
    await wrapper.find(".ac-canvas-viewport").trigger("click");
    await wrapper.vm.$nextTick();
    expect(wrapper.findAll(".ac-node.selected")).toHaveLength(0);
  });

  // ── 详情区组合（阶段 3 S2 增补） ──

  it("shows node detail when a canvas node is selected", async () => {
    const agents = useAgentsStore();
    agents.onAgentSpawned(spawn("alpha"));
    setActiveChain("alpha", [
      {
        id: "a1",
        parent_id: null,
        timestamp: "t1",
        ability_names: ["write_file"],
        action_results: [
          {
            ability_name: "write_file",
            action_result: { outcome: "success", data: { file_path: "src/foo.py" } },
          },
        ],
      },
      { id: "a2", parent_id: "a1", timestamp: "t2" },
    ]);
    const wrapper = mount(ActionChainPanel, { props: { agent: target("alpha") } });
    await wrapper.vm.$nextTick();
    // 未选中时占位提示
    expect(wrapper.find(".ac-detail--empty").exists()).toBe(true);

    await wrapper.findAll(".ac-node")[0].trigger("click");
    await wrapper.vm.$nextTick();
    const detail = wrapper.find(".ac-detail");
    expect(detail.exists()).toBe(true);
    expect(wrapper.find(".ac-detail--empty").exists()).toBe(false);
    // write_file 节点渲染出 file_path 表格
    expect(detail.text()).toContain("src/foo.py");
    expect(wrapper.findAll(".ac-node.selected")).toHaveLength(1);

    // 再次点击背景清空选中与详情
    await wrapper.find(".ac-canvas-viewport").trigger("click");
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".ac-detail--empty").exists()).toBe(true);
  });

  it("clears detail when the viewed session switches", async () => {
    const agents = useAgentsStore();
    const sessions = useSessionsStore();
    agents.onAgentSpawned(spawn("alpha"));
    const agent = target("alpha");
    // setActiveChain 会重置 agent 的 session 列表，先建第一条链再补第二个 Session
    setActiveChain("alpha", [{ id: "a1", parent_id: null, timestamp: "t1" }]);
    sessions.replaceAgentSessions(agent, [
      SessionInfoPayloadSchema.parse({
        session_id: "alpha-session",
        agent_name: "alpha",
        root_node_id: "a1",
        active_branch_id: "alpha-branch",
      }),
      SessionInfoPayloadSchema.parse({
        session_id: "alpha-session-2",
        agent_name: "alpha",
        root_node_id: "z1",
        active_branch_id: "alpha-branch",
      }),
    ]);
    sessions.setActiveSession(agent, "alpha-session");
    chains_setup2();
    const wrapper = mount(ActionChainPanel, { props: { agent: target("alpha") } });
    await wrapper.vm.$nextTick();
    await wrapper.findAll(".ac-node")[0].trigger("click");
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".ac-detail").exists()).toBe(true);
    expect(wrapper.find(".ac-detail--empty").exists()).toBe(false);

    // 切换 viewed Session：详情随选中更新（选中节点不在新链中 → 占位）
    await wrapper.get<HTMLSelectElement>(".chain-session-select").setValue("alpha-session-2");
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".ac-detail--empty").exists()).toBe(true);
  });

  /** 第二个 Session（alpha-session-2）的单节点链与 branch 夹具。 */
  function chains_setup2(): void {
    const session: SessionTarget = { ...target("alpha"), sessionId: "alpha-session-2" };
    useBranchesStore().replaceSessionBranches(
      session,
      [
        BranchInfoPayloadSchema.parse({
          branch_id: "alpha-branch",
          session_id: "alpha-session-2",
          name: "main",
          head_node_id: "z1",
        }),
      ],
      { explicitActiveBranchId: "alpha-branch" },
    );
    useActionChainsStore().setChain({ ...session, branchId: "alpha-branch" }, [
      node({
        id: "z1",
        parent_id: null,
        timestamp: "t9",
        session_id: "alpha-session-2",
        created_on_branch_id: "alpha-branch",
      }),
    ]);
  }

  // ── S3：管理操作（归档/删除）与从此节点重试 ──

  it("archives the viewed session with confirmation and correct scope", async () => {
    const agents = useAgentsStore();
    agents.onAgentSpawned(spawn("alpha"));
    setActiveChain("alpha", [{ id: "a1", parent_id: null, timestamp: "t1" }]);
    const wrapper = mount(ActionChainPanel, { props: { agent: target("alpha") } });
    await wrapper.vm.$nextTick();

    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    await wrapper.get("button.chain-archive-session").trigger("click");
    expect(confirm).toHaveBeenCalledOnce();
    expect(archiveSession).toHaveBeenCalledWith({
      ...target("alpha"),
      sessionId: "alpha-session",
    });

    // 拒绝确认不发命令
    confirm.mockReturnValue(false);
    await wrapper.get("button.chain-archive-session").trigger("click");
    expect(archiveSession).toHaveBeenCalledTimes(1);
  });

  it("archives and deletes the viewed branch with correct scope", async () => {
    const agents = useAgentsStore();
    const branches = useBranchesStore();
    agents.onAgentSpawned(spawn("alpha"));
    const agent = target("alpha");
    const session: SessionTarget = { ...agent, sessionId: "alpha-session" };
    sessions_setupTwoBranches();
    const wrapper = mount(ActionChainPanel, { props: { agent } });
    await wrapper.vm.$nextTick();

    // viewed Branch 初始回退 runtime（alpha-branch），切到第二个 Branch 后操作
    await wrapper.get<HTMLSelectElement>(".chain-branch-select").setValue("alpha-branch-2");
    vi.spyOn(window, "confirm").mockReturnValue(true);
    await wrapper.get("button.chain-archive-branch").trigger("click");
    expect(archiveBranch).toHaveBeenCalledWith({ ...session, branchId: "alpha-branch-2" });

    await wrapper.get("button.chain-delete-branch").trigger("click");
    expect(deleteBranch).toHaveBeenCalledWith({ ...session, branchId: "alpha-branch-2" });

    // 运行 Branch（runtime = alpha-branch）删除禁用
    await wrapper.get<HTMLSelectElement>(".chain-branch-select").setValue("alpha-branch");
    const deleteBtn = wrapper.get<HTMLButtonElement>("button.chain-delete-branch");
    expect(deleteBtn.attributes("disabled")).toBeDefined();
    expect(branches.activeBranchId(session)).toBe("alpha-branch");
  });

  it("disables deleting the running session", async () => {
    const agents = useAgentsStore();
    const sessions = useSessionsStore();
    agents.onAgentSpawned(spawn("alpha"));
    setActiveChain("alpha", [{ id: "a1", parent_id: null, timestamp: "t1" }]);
    const wrapper = mount(ActionChainPanel, { props: { agent: target("alpha") } });
    await wrapper.vm.$nextTick();
    // 单 Session：viewed = runtime → 删除禁用
    expect(sessions.activeSessionId(target("alpha"))).toBe("alpha-session");
    const btn = wrapper.get<HTMLButtonElement>("button.chain-delete-session");
    expect(btn.attributes("disabled")).toBeDefined();
  });

  it("deletes a non-running session with confirmation", async () => {
    const agents = useAgentsStore();
    const sessions = useSessionsStore();
    agents.onAgentSpawned(spawn("alpha"));
    const agent = target("alpha");
    setActiveChain("alpha", [{ id: "a1", parent_id: null, timestamp: "t1" }]);
    sessions.replaceAgentSessions(agent, [
      SessionInfoPayloadSchema.parse({
        session_id: "alpha-session",
        agent_name: "alpha",
        root_node_id: "a1",
        active_branch_id: "alpha-branch",
      }),
      SessionInfoPayloadSchema.parse({
        session_id: "alpha-session-2",
        agent_name: "alpha",
        root_node_id: "z1",
        active_branch_id: "alpha-branch",
      }),
    ]);
    sessions.setActiveSession(agent, "alpha-session");
    chains_setup2();

    const wrapper = mount(ActionChainPanel, { props: { agent } });
    await wrapper.vm.$nextTick();
    await wrapper.get<HTMLSelectElement>(".chain-session-select").setValue("alpha-session-2");
    vi.spyOn(window, "confirm").mockReturnValue(true);
    await wrapper.get("button.chain-delete-session").trigger("click");
    expect(deleteSession).toHaveBeenCalledWith({ ...agent, sessionId: "alpha-session-2" });
    // 运行态不被触碰
    expect(sessions.activeSessionId(agent)).toBe("alpha-session");
  });

  it("retries from the selected node: createBranch(fromNodeId) only, no implicit activate", async () => {
    const agents = useAgentsStore();
    const branches = useBranchesStore();
    agents.onAgentSpawned(spawn("alpha"));
    setActiveChain("alpha", [
      { id: "a1", parent_id: null, timestamp: "t1" },
      { id: "a2", parent_id: "a1", timestamp: "t2" },
    ]);
    createBranch.mockResolvedValue({ success: true, data: { branch_id: "alpha-branch-2" } });

    const wrapper = mount(ActionChainPanel, { props: { agent: target("alpha") } });
    await wrapper.vm.$nextTick();
    // 未选中节点时无重试入口
    expect(wrapper.find("button.chain-retry-from-node").exists()).toBe(false);

    await wrapper.findAll(".ac-node")[1].trigger("click");
    await wrapper.vm.$nextTick();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    await wrapper.get("button.chain-retry-from-node").trigger("click");

    // 重试 = createBranch(fromNodeId)，绝不 sessionCreate / activate*
    const session: SessionTarget = { ...target("alpha"), sessionId: "alpha-session" };
    expect(createBranch).toHaveBeenCalledWith(session, "retry-2", { fromNodeId: "a2" });
    expect(createSession).not.toHaveBeenCalled();
    expect(activateSession).not.toHaveBeenCalled();
    expect(activateBranch).not.toHaveBeenCalled();
    // 成功后仅 viewBranch 切展示（D6）
    expect(branches.viewedBranchId(session)).toBe("alpha-branch-2");
    // 运行 Branch 不动
    expect(branches.activeBranchId(session)).toBe("alpha-branch");
  });

  it("shows failure receipts for management actions without moving viewed/runtime pointers", async () => {
    const agents = useAgentsStore();
    const sessions = useSessionsStore();
    agents.onAgentSpawned(spawn("alpha"));
    setActiveChain("alpha", [
      { id: "a1", parent_id: null, timestamp: "t1" },
      { id: "a2", parent_id: "a1", timestamp: "t2" },
    ]);
    const agent = target("alpha");
    const session: SessionTarget = { ...agent, sessionId: "alpha-session" };

    // 归档失败：回执展示，列表不移除、viewed 不动
    archiveSession.mockResolvedValue({
      success: false,
      error: "session is active",
      error_detail: "cannot archive the active session",
    });
    const wrapper = mount(ActionChainPanel, { props: { agent } });
    await wrapper.vm.$nextTick();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    await wrapper.get("button.chain-archive-session").trigger("click");
    await wrapper.vm.$nextTick();
    expect(wrapper.get('[role="alert"]').text()).toContain("cannot archive the active session");
    expect(sessions.sessionsForAgent(agent).map((s) => s.target.sessionId)).toContain(
      "alpha-session",
    );

    // 重试失败：回执展示，不切 viewed Branch
    createBranch.mockResolvedValue({
      success: false,
      error: "fork node not found",
      error_detail: "fork node is not readable",
    });
    await wrapper.findAll(".ac-node")[0].trigger("click");
    await wrapper.vm.$nextTick();
    await wrapper.get("button.chain-retry-from-node").trigger("click");
    await wrapper.vm.$nextTick();
    expect(wrapper.get('[role="alert"]').text()).toContain("fork node is not readable");
    expect(useBranchesStore().viewedBranchId(session)).not.toBe("alpha-branch-2");
  });

  // ── S2：重置 Agent（agent_reset 面板入口） ──

  it("resets the agent: confirm gates the command, receipt drives notice only", async () => {
    const agents = useAgentsStore();
    const sessions = useSessionsStore();
    agents.onAgentSpawned(spawn("alpha"));
    setActiveChain("alpha", [{ id: "a1", parent_id: null, timestamp: "t1" }]);
    agentReset.mockResolvedValue({
      success: true,
      data: { session_id: "alpha-session-2", branch_id: "alpha-branch-2", root_node_id: "r1" },
    });

    const wrapper = mount(ActionChainPanel, { props: { agent: target("alpha") } });
    await wrapper.vm.$nextTick();

    // confirm 拒绝 → 零调用
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    await wrapper.get("button.chain-reset-agent").trigger("click");
    expect(confirm).toHaveBeenCalledOnce();
    expect(agentReset).not.toHaveBeenCalled();

    // 确认 → agentReset(projectId, agentId 口径的 AgentTarget) 恰一次
    confirm.mockReturnValue(true);
    await wrapper.get("button.chain-reset-agent").trigger("click");
    expect(agentReset).toHaveBeenCalledTimes(1);
    expect(agentReset).toHaveBeenCalledWith(target("alpha"));
    await wrapper.vm.$nextTick();
    // 成功提示来自回执 data（仅提示；实体/运行标记由事件面推进）
    expect(wrapper.text()).toContain("alpha-session-2");
    // 面板不发运行时切换命令，viewed 指针不动
    expect(activateSession).not.toHaveBeenCalled();
    expect(activateBranch).not.toHaveBeenCalled();
    expect(sessions.viewedSessionId(target("alpha"))).toBe("alpha-session");
  });

  it("shows reset failure receipts without moving viewed/runtime pointers", async () => {
    const agents = useAgentsStore();
    const sessions = useSessionsStore();
    agents.onAgentSpawned(spawn("alpha"));
    setActiveChain("alpha", [{ id: "a1", parent_id: null, timestamp: "t1" }]);
    const agent = target("alpha");
    agentReset.mockResolvedValue({
      success: false,
      error: "reset rejected",
      error_detail: "Cannot reset agent 'alpha' while its drive loop is active",
    });

    const wrapper = mount(ActionChainPanel, { props: { agent } });
    await wrapper.vm.$nextTick();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    await wrapper.get("button.chain-reset-agent").trigger("click");
    await wrapper.vm.$nextTick();

    expect(wrapper.get('[role="alert"]').text()).toContain(
      "Cannot reset agent 'alpha' while its drive loop is active",
    );
    // 失败回执不改 viewed/runtime 指针
    expect(sessions.viewedSessionId(agent)).toBe("alpha-session");
    expect(sessions.activeSessionId(agent)).toBe("alpha-session");
    // 无成功提示
    expect(wrapper.text()).not.toContain("resetAgentDone");
  });

  /** 双 Branch 夹具：alpha-branch（runtime，head=root）+ alpha-branch-2。 */
  function sessions_setupTwoBranches(): void {
    const agent = target("alpha");
    const session: SessionTarget = { ...agent, sessionId: "alpha-session" };
    useSessionsStore().replaceAgentSessions(agent, [
      SessionInfoPayloadSchema.parse({
        session_id: "alpha-session",
        agent_name: "alpha",
        root_node_id: "root",
        active_branch_id: "alpha-branch",
      }),
    ]);
    useSessionsStore().setActiveSession(agent, "alpha-session");
    useBranchesStore().replaceSessionBranches(
      session,
      [
        BranchInfoPayloadSchema.parse({
          branch_id: "alpha-branch",
          session_id: "alpha-session",
          name: "Branch 1",
          head_node_id: "root",
        }),
        BranchInfoPayloadSchema.parse({
          branch_id: "alpha-branch-2",
          session_id: "alpha-session",
          name: "Branch 2",
          head_node_id: "root",
        }),
      ],
      { explicitActiveBranchId: "alpha-branch" },
    );
  }

  // ── S4：layout 增量缓存（append 引用复用 + 缓存键隔离） ──

  it("appended nodes reuse the cached layout without full rebuild (store-driven append)", async () => {
    const agents = useAgentsStore();
    const chains = useActionChainsStore();
    const branches = useBranchesStore();
    agents.onAgentSpawned(spawn("alpha"));
    setActiveChain("alpha", [
      { id: "a1", parent_id: null, timestamp: "t1" },
      { id: "a2", parent_id: "a1", timestamp: "t2" },
    ]);
    const wrapper = mount(ActionChainPanel, { props: { agent: target("alpha") } });
    await wrapper.vm.$nextTick();
    expect(wrapper.findAll(".ac-node")).toHaveLength(2);

    // store 端事件追加（mergeNodes 保引用 → append-only 前缀）+ head 前移（commit 语义）
    const session: SessionTarget = { ...target("alpha"), sessionId: "alpha-session" };
    chains.onActionChainUpdated({
      type: "action_chain_updated",
      project_id: "p1",
      cluster_id: "cluster",
      agent_id: "alpha-id",
      agent_name: "alpha",
      node: node({
        id: "a3",
        parent_id: "a2",
        session_id: "alpha-session",
        created_on_branch_id: "alpha-branch",
        timestamp: "t3",
      }),
    } as Parameters<typeof chains.onActionChainUpdated>[0]);
    branches.advanceHead(
      { ...session, branchId: "alpha-branch" },
      BranchInfoPayloadSchema.parse({
        branch_id: "alpha-branch",
        session_id: "alpha-session",
        name: "main",
        head_node_id: "a3",
      }),
      node({
        id: "a3",
        parent_id: "a2",
        session_id: "alpha-session",
        created_on_branch_id: "alpha-branch",
        timestamp: "t3",
      }),
    );
    await wrapper.vm.$nextTick();
    const before = wrapper.findAll(".ac-node");
    expect(before).toHaveLength(3);

    // 再追加一个节点：增量命中（旧 LayoutNode 引用复用 → Vue keyed diff 旧 DOM 不动）
    chains.onActionChainUpdated({
      type: "action_chain_updated",
      project_id: "p1",
      cluster_id: "cluster",
      agent_id: "alpha-id",
      agent_name: "alpha",
      node: node({
        id: "a4",
        parent_id: "a3",
        session_id: "alpha-session",
        created_on_branch_id: "alpha-branch",
        timestamp: "t4",
      }),
    } as Parameters<typeof chains.onActionChainUpdated>[0]);
    branches.advanceHead(
      { ...session, branchId: "alpha-branch" },
      BranchInfoPayloadSchema.parse({
        branch_id: "alpha-branch",
        session_id: "alpha-session",
        name: "main",
        head_node_id: "a4",
      }),
      node({
        id: "a4",
        parent_id: "a3",
        session_id: "alpha-session",
        created_on_branch_id: "alpha-branch",
        timestamp: "t4",
      }),
    );
    await wrapper.vm.$nextTick();
    const after = wrapper.findAll(".ac-node");
    expect(after).toHaveLength(4);
    // 旧节点 DOM 元素引用保持（keyed diff 跳过 = 缓存命中的渲染层证据）
    expect(after[0]!.element).toBe(before[0]!.element);
    expect(after[1]!.element).toBe(before[1]!.element);
    expect(after[2]!.element).toBe(before[2]!.element);
  });

  it("switching branch selection rebuilds via cache keys without cross-scope leaks", async () => {
    const agents = useAgentsStore();
    const chains = useActionChainsStore();
    const branches = useBranchesStore();
    agents.onAgentSpawned(spawn("alpha"));
    const agent = target("alpha");
    const session: SessionTarget = { ...agent, sessionId: "alpha-session" };
    sessions_setupTwoBranches();
    // alpha-branch: a1→a2；alpha-branch-2: b1→b2
    chains.setChain({ ...session, branchId: "alpha-branch" }, [
      node({
        id: "a1",
        parent_id: null,
        timestamp: "t1",
        session_id: "alpha-session",
        created_on_branch_id: "alpha-branch",
      }),
      node({
        id: "a2",
        parent_id: "a1",
        timestamp: "t2",
        session_id: "alpha-session",
        created_on_branch_id: "alpha-branch",
      }),
    ]);
    chains.setChain({ ...session, branchId: "alpha-branch-2" }, [
      node({
        id: "b1",
        parent_id: null,
        timestamp: "t1",
        session_id: "alpha-session",
        created_on_branch_id: "alpha-branch-2",
      }),
      node({
        id: "b2",
        parent_id: "b1",
        timestamp: "t2",
        session_id: "alpha-session",
        created_on_branch_id: "alpha-branch-2",
      }),
      node({
        id: "b3",
        parent_id: "b2",
        timestamp: "t3",
        session_id: "alpha-session",
        created_on_branch_id: "alpha-branch-2",
      }),
    ]);
    // head 修正（setActiveChain 未用，手工对齐 branch head）
    branches.replaceSessionBranches(
      session,
      [
        BranchInfoPayloadSchema.parse({
          branch_id: "alpha-branch",
          session_id: "alpha-session",
          name: "Branch 1",
          head_node_id: "a2",
        }),
        BranchInfoPayloadSchema.parse({
          branch_id: "alpha-branch-2",
          session_id: "alpha-session",
          name: "Branch 2",
          head_node_id: "b3",
        }),
      ],
      { explicitActiveBranchId: "alpha-branch" },
    );

    const wrapper = mount(ActionChainPanel, { props: { agent } });
    await wrapper.vm.$nextTick();
    // runtime 回退：默认展示 alpha-branch（a1→a2）；节点 transform 携带其布局坐标
    const transformOf = () => wrapper.findAll(".ac-node").map((n) => n.attributes("transform"));
    expect(wrapper.findAll(".ac-node")).toHaveLength(2);
    const branch1Transforms = transformOf();

    // 切到 alpha-branch-2（缓存键变化 → 独立建图）再切回：内容正确不串扰
    await wrapper.get<HTMLSelectElement>(".chain-branch-select").setValue("alpha-branch-2");
    await wrapper.vm.$nextTick();
    expect(wrapper.findAll(".ac-node")).toHaveLength(3);
    expect(transformOf()).not.toEqual(branch1Transforms);

    await wrapper.get<HTMLSelectElement>(".chain-branch-select").setValue("alpha-branch");
    await wrapper.vm.$nextTick();
    expect(transformOf()).toEqual(branch1Transforms);
  });

  // ── 画布横向视口：链自左向右生长，默认停在 Head 端 ──

  it("opens at the rightmost head so the latest node is visible", async () => {
    const agents = useAgentsStore();
    agents.onAgentSpawned(spawn("alpha"));
    setActiveChain("alpha", [
      { id: "a1", parent_id: null, timestamp: "t1" },
      { id: "a2", parent_id: "a1", timestamp: "t2" },
    ]);
    const wrapper = mount(ActionChainPanel, { props: { agent: target("alpha") } });
    const viewport = wrapper.find(".ac-canvas-viewport").element as HTMLElement;
    mockViewportScroll(viewport, { scrollWidth: 1000, clientWidth: 200 });
    await wrapper.vm.$nextTick();
    await wrapper.vm.$nextTick();
    expect(viewport.scrollLeft).toBe(800); // 1000 - 200：Head 可见
    wrapper.unmount();
  });

  it("follows appended nodes while at the right end but not after scrolling back", async () => {
    const agents = useAgentsStore();
    agents.onAgentSpawned(spawn("alpha"));
    setActiveChain("alpha", [
      { id: "a1", parent_id: null, timestamp: "t1" },
      { id: "a2", parent_id: "a1", timestamp: "t2" },
    ]);
    const wrapper = mount(ActionChainPanel, { props: { agent: target("alpha") } });
    const viewport = wrapper.find(".ac-canvas-viewport").element as HTMLElement;
    const metrics = { scrollWidth: 1000, clientWidth: 200 };
    mockViewportScroll(viewport, metrics);
    await wrapper.vm.$nextTick();
    await wrapper.vm.$nextTick();
    expect(viewport.scrollLeft).toBe(800);

    // 贴右时新节点到达（Head 前移）：继续跟随 Head
    metrics.scrollWidth = 1200;
    setActiveChain("alpha", [
      { id: "a1", parent_id: null, timestamp: "t1" },
      { id: "a2", parent_id: "a1", timestamp: "t2" },
      { id: "a3", parent_id: "a2", timestamp: "t3" },
    ]);
    await wrapper.vm.$nextTick();
    await wrapper.vm.$nextTick();
    expect(viewport.scrollLeft).toBe(1000);

    // 用户左翻查看历史：新节点不得把视口拽回右端
    viewport.scrollLeft = 300;
    viewport.dispatchEvent(new Event("scroll"));
    await wrapper.vm.$nextTick();
    metrics.scrollWidth = 1600;
    setActiveChain("alpha", [
      { id: "a1", parent_id: null, timestamp: "t1" },
      { id: "a2", parent_id: "a1", timestamp: "t2" },
      { id: "a3", parent_id: "a2", timestamp: "t3" },
      { id: "a4", parent_id: "a3", timestamp: "t4" },
    ]);
    await wrapper.vm.$nextTick();
    await wrapper.vm.$nextTick();
    expect(viewport.scrollLeft).toBe(300);
    wrapper.unmount();
  });

  it("returns to the rightmost end when switching the viewed session", async () => {
    const agents = useAgentsStore();
    const sessions = useSessionsStore();
    agents.onAgentSpawned(spawn("alpha"));
    const agent = target("alpha");
    setActiveChain("alpha", [
      { id: "a1", parent_id: null, timestamp: "t1" },
      { id: "a2", parent_id: "a1", timestamp: "t2" },
    ]);
    sessions.replaceAgentSessions(agent, [
      SessionInfoPayloadSchema.parse({
        session_id: "alpha-session",
        agent_name: "alpha",
        root_node_id: "a1",
        active_branch_id: "alpha-branch",
      }),
      SessionInfoPayloadSchema.parse({
        session_id: "alpha-session-2",
        agent_name: "alpha",
        root_node_id: "z1",
        active_branch_id: "alpha-branch",
      }),
    ]);
    sessions.setActiveSession(agent, "alpha-session");
    chains_setup2();

    const wrapper = mount(ActionChainPanel, { props: { agent: target("alpha") } });
    const viewport = wrapper.find(".ac-canvas-viewport").element as HTMLElement;
    mockViewportScroll(viewport, { scrollWidth: 1000, clientWidth: 200 });
    await wrapper.vm.$nextTick();
    await wrapper.vm.$nextTick();
    expect(viewport.scrollLeft).toBe(800);

    viewport.scrollLeft = 300; // 在第一条链上左翻
    viewport.dispatchEvent(new Event("scroll"));
    await wrapper.vm.$nextTick();

    // 切换 viewed Session = 打开另一条链：回到 Head 端
    await wrapper.get<HTMLSelectElement>(".chain-session-select").setValue("alpha-session-2");
    await wrapper.vm.$nextTick();
    await wrapper.vm.$nextTick();
    expect(viewport.scrollLeft).toBe(800);
  });
});
