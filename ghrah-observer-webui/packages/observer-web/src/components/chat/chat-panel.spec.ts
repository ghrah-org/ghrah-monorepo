// @vitest-environment happy-dom

import { useChatStore, useConnectionStore, useRoomsStore } from "@ghrah/observer-core";
import type { RoomInfoPayload, RoomLogEntryPayload } from "@ghrah/protocol";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

const roomSendMock = vi.fn();
const getRoomLogMock = vi.fn();
vi.mock("@/composables/useObserver", () => ({
  useObserver: () => ({ roomSend: roomSendMock, getRoomLog: getRoomLogMock }),
}));

import ChatPanel from "./chat-panel.vue";

function room(id: string, name: string, agents: string[] = []): RoomInfoPayload {
  return {
    room_id: id,
    project_id: "p1",
    name,
    status: "active",
    members: agents.map((a) => ({
      subject: a,
      subject_type: "agent" as const,
      subject_name: `Name-${a}`,
      joined_at: "",
    })),
    seq_watermark: 0,
    version: 1,
    created_at: "",
    updated_at: "",
    archived_at: null,
  };
}

function logEntry(
  roomId: string,
  seq: number,
  author: string,
  authorType: "agent" | "human",
  message: string,
  data: Record<string, unknown> = {},
): RoomLogEntryPayload {
  return {
    id: `${roomId}-${seq}`,
    room_id: roomId,
    seq,
    author,
    author_type: authorType,
    timestamp: 1767225600,
    data: { message, ...data },
  };
}

function setup() {
  const rooms = useRoomsStore();
  rooms.replaceProjectRooms("p1", [room("r1", "arch"), room("r2", "frontend")], "active");
  const chat = useChatStore();
  const connection = useConnectionStore();
  connection.setConnected();
  return { rooms, chat, connection };
}

function mountPanel(roomId = "r1") {
  return mount(ChatPanel, { props: { projectId: "p1", roomId } });
}

// happy-dom 无真实布局（scrollHeight 恒 0），模拟贴底/上翻视口
function mockViewport(el: HTMLElement, scrollHeight: number, clientHeight: number, top: number) {
  let scrollTop = top;
  Object.defineProperty(el, "scrollHeight", { configurable: true, get: () => scrollHeight });
  Object.defineProperty(el, "clientHeight", { configurable: true, get: () => clientHeight });
  Object.defineProperty(el, "scrollTop", {
    configurable: true,
    get: () => scrollTop,
    set: (v: number) => {
      scrollTop = v;
    },
  });
}

describe("ChatPanel", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    roomSendMock.mockReset();
  });

  it("shows empty state when room does not exist", () => {
    setup();
    const wrapper = mountPanel("missing");
    expect(wrapper.text()).toContain("Select a room to start chatting");
  });

  it("shows room name in header", async () => {
    setup();
    const wrapper = mountPanel("r1");
    await wrapper.vm.$nextTick();
    expect(wrapper.find("h3").text()).toContain("arch");
  });

  it("renders entries of the target room only", async () => {
    const { rooms } = setup();
    rooms.setRoomLog("r1", [
      logEntry("r1", 1, "architect", "agent", "hello from agent"),
      logEntry("r1", 2, "user", "human", "hello from human"),
    ]);
    const wrapper = mountPanel("r1");
    await wrapper.vm.$nextTick();
    const entries = wrapper.findAll(".chat-entry");
    expect(entries).toHaveLength(2);
    expect(entries[0].classes()).toContain("entry-conversation");
    expect(entries[0].text()).toContain("hello from agent");
    expect(entries[1].classes()).toContain("entry-human");
    expect(entries[1].text()).toContain("hello from human");
  });

  it("end_task 结果投影为结束态条目并渲染（不再被当成普通会话）", async () => {
    const { rooms } = setup();
    rooms.setRoomLog("r1", [
      logEntry("r1", 1, "architect", "agent", "开始梳理", { ability: "conversation" }),
      logEntry("r1", 2, "architect", "agent", "梳理完成", { ability: "end_task" }),
    ]);
    const wrapper = mountPanel("r1");
    await wrapper.vm.$nextTick();
    const entries = wrapper.findAll(".chat-entry");
    expect(entries).toHaveLength(2);
    expect(entries[0].classes()).toContain("entry-conversation");
    expect(entries[1].classes()).toContain("entry-endtask");
    expect(entries[1].text()).toContain("梳理完成");
    expect(entries[1].find(".entry-header").text()).toContain("✓");
  });

  it("merges pending entries of the target room only", async () => {
    const { rooms, chat } = setup();
    rooms.setRoomLog("r1", [logEntry("r1", 1, "architect", "agent", "history")]);
    chat.addPendingEntry({
      projectId: "p1",
      to: "r1",
      content: "pending-r1",
      agentName: "",
      roomId: "r1",
    });
    chat.addPendingEntry({
      projectId: "p1",
      to: "r2",
      content: "pending-r2",
      agentName: "",
      roomId: "r2",
    });
    const wrapper = mountPanel("r1");
    await wrapper.vm.$nextTick();
    const entries = wrapper.findAll(".chat-entry");
    expect(entries).toHaveLength(2);
    expect(wrapper.text()).toContain("history");
    expect(wrapper.text()).toContain("pending-r1");
    expect(wrapper.text()).not.toContain("pending-r2");
    expect(entries[1].classes()).toContain("entry-pending");
  });

  it("handleSend calls roomSend and adds pending entry", async () => {
    const { rooms, chat } = setup();
    rooms.setRoomLog("r1", [logEntry("r1", 1, "architect", "agent", "history")]);
    roomSendMock.mockResolvedValueOnce({ success: true });
    const wrapper = mountPanel("r1");
    await wrapper.vm.$nextTick();
    await wrapper.find("textarea").setValue("hello room");
    await wrapper.find("form").trigger("submit");
    expect(roomSendMock).toHaveBeenCalledWith("r1", "hello room", []);
    const pending = chat.allEntries.find((e) => e.content === "hello room");
    expect(pending?.roomId).toBe("r1");
    expect(pending?.pending).toBe(true);
  });

  it("pending entry is removed when echo arrives and history shows instead", async () => {
    const { rooms, chat } = setup();
    rooms.setRoomLog("r1", [logEntry("r1", 1, "architect", "agent", "history")]);
    roomSendMock.mockResolvedValueOnce({ success: true });
    const wrapper = mountPanel("r1");
    await wrapper.vm.$nextTick();
    await wrapper.find("textarea").setValue("echo me");
    await wrapper.find("form").trigger("submit");
    expect(wrapper.text()).toContain("echo me");
    // 模拟 bind 的 ROOM_LOG_APPENDED 分发：rooms 追加 + chat echo 确认
    const echo = logEntry("r1", 2, "user", "human", "echo me");
    rooms.onRoomLogAppended({ entry: echo });
    chat.onRoomLogAppended(echo, "p1");
    await wrapper.vm.$nextTick();
    expect(chat.allEntries.find((e) => e.content === "echo me")).toBeUndefined();
    const entries = wrapper.findAll(".chat-entry");
    expect(entries).toHaveLength(2);
    expect(entries[1].classes()).toContain("entry-human");
    expect(entries[1].classes()).not.toContain("entry-pending");
  });

  it("marks pending error when roomSend returns null", async () => {
    const { rooms, chat } = setup();
    rooms.setRoomLog("r1", [logEntry("r1", 1, "architect", "agent", "history")]);
    roomSendMock.mockResolvedValueOnce(null);
    const wrapper = mountPanel("r1");
    await wrapper.vm.$nextTick();
    await wrapper.find("textarea").setValue("boom");
    await wrapper.find("form").trigger("submit");
    await vi.waitFor(() => {
      expect(chat.allEntries.some((e) => e.error)).toBe(true);
    });
    const e = chat.allEntries.find((x) => x.error);
    expect(e?.error).toContain("Send failed");
    expect(e?.roomId).toBe("r1");
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".entry-error").exists()).toBe(true);
  });

  it("targeted send: chip 选中后 roomSend 携带 targets", async () => {
    const { rooms } = setup();
    rooms.replaceProjectRooms("p1", [room("r1", "arch", ["frontend", "backend"])], "active");
    rooms.setRoomLog("r1", [logEntry("r1", 1, "architect", "agent", "history")]);
    roomSendMock.mockResolvedValueOnce({ success: true });
    const wrapper = mountPanel("r1");
    await wrapper.vm.$nextTick();
    const chips = wrapper.findAll('button[type="button"]');
    await chips[0].trigger("click"); // @frontend
    await wrapper.find("textarea").setValue("please review");
    await wrapper.find("form").trigger("submit");
    expect(roomSendMock).toHaveBeenCalledWith("r1", "please review", ["frontend"]);
  });

  it("entry 带 targets 时 header 显示定向标记", async () => {
    const { rooms } = setup();
    rooms.setRoomLog("r1", [
      logEntry("r1", 1, "architect", "agent", "targeted msg", { targets: ["frontend"] }),
      logEntry("r1", 2, "architect", "agent", "broadcast msg"),
    ]);
    const wrapper = mountPanel("r1");
    await wrapper.vm.$nextTick();
    const headers = wrapper.findAll(".entry-header");
    expect(headers[0].text()).toContain("→ @frontend");
    expect(headers[1].text()).not.toContain("→");
  });

  it("author/targets 为稳定 ID 时 header 解析为成员显示名", async () => {
    const { rooms } = setup();
    rooms.replaceProjectRooms(
      "p1",
      [room("r1", "arch", ["447c4313e38242fb8e173bcb68e183ea", "b2a1"])],
      "active",
    );
    rooms.setRoomLog("r1", [
      logEntry("r1", 1, "447c4313e38242fb8e173bcb68e183ea", "agent", "reply", {
        targets: ["b2a1"],
      }),
      logEntry("r1", 2, "user", "human", "ask", {
        targets: ["447c4313e38242fb8e173bcb68e183ea"],
      }),
    ]);
    const wrapper = mountPanel("r1");
    await wrapper.vm.$nextTick();
    const headers = wrapper.findAll(".entry-header");
    expect(headers[0].text()).toBe("@Name-447c4313e38242fb8e173bcb68e183ea → @Name-b2a1");
    expect(headers[1].text()).toContain("→ @Name-447c4313e38242fb8e173bcb68e183ea");
    expect(headers[1].text()).not.toContain("@447c4313e38242fb8e173bcb68e183ea→");
  });

  it("does not send when input is empty", async () => {
    const { rooms } = setup();
    rooms.setRoomLog("r1", [logEntry("r1", 1, "architect", "agent", "history")]);
    const wrapper = mountPanel("r1");
    await wrapper.vm.$nextTick();
    await wrapper.find("form").trigger("submit");
    expect(roomSendMock).not.toHaveBeenCalled();
  });

  it("无块回退分支走 Markdown 渲染", async () => {
    const { rooms } = setup();
    rooms.setRoomLog("r1", [logEntry("r1", 1, "architect", "agent", "**bold** log entry")]);
    const wrapper = mountPanel("r1");
    await wrapper.vm.$nextTick();
    const body = wrapper.find(".chat-entry .markdown-body");
    expect(body.exists()).toBe(true);
    expect(body.html()).toContain("<strong>bold</strong>");
  });

  it("贴底时新消息自动滚底，上翻时不打断", async () => {
    const { rooms } = setup();
    rooms.setRoomLog("r1", [logEntry("r1", 1, "architect", "agent", "first")]);
    const wrapper = mountPanel("r1");
    await wrapper.vm.$nextTick();
    const container = wrapper.find(".flex-1.overflow-y-auto");
    expect(container.exists()).toBe(true);
    const el = container.element as HTMLElement;

    // 贴底视口：追加消息 → sticky 生效，scrollToBottom 落到 scrollHeight
    mockViewport(el, 1000, 200, 800);
    await container.trigger("scroll");
    rooms.onRoomLogAppended({ entry: logEntry("r1", 2, "architect", "agent", "second") });
    await wrapper.vm.$nextTick();
    await wrapper.vm.$nextTick();
    expect(el.scrollTop).toBe(1000);

    // 上翻浏览历史：追加消息 → 位置不被强制滚底
    mockViewport(el, 2000, 200, 300);
    await container.trigger("scroll");
    rooms.onRoomLogAppended({ entry: logEntry("r1", 3, "architect", "agent", "third") });
    await wrapper.vm.$nextTick();
    await wrapper.vm.$nextTick();
    expect(el.scrollTop).toBe(300);
    wrapper.unmount();
  });
});

it("shows authoritative delivery failure", async () => {
  vi.useFakeTimers();
  try {
    const { rooms } = setup();
    rooms.setRoomLog("r1", [
      logEntry("r1", 1, "user", "human", "question", {
        deliveries: [
          {
            delivery_id: "d1",
            recipient_id: "a1",
            state: "failed",
            error: "model_configuration_invalid",
          },
        ],
      }),
    ]);
    const w = mountPanel();
    expect(w.text()).toContain("model_configuration_invalid");
    expect(w.text()).toContain("Failed");
    w.unmount();
  } finally {
    vi.useRealTimers();
  }
});

it("refreshes pending delivery status and stops polling after unmount", async () => {
  vi.useFakeTimers();
  getRoomLogMock.mockClear().mockResolvedValue({ success: true });
  try {
    const { rooms } = setup();
    rooms.setRoomLog("r1", [
      logEntry("r1", 1, "user", "human", "question", {
        deliveries: [{ delivery_id: "d", recipient_id: "a", state: "processing" }],
      }),
    ]);
    const w = mountPanel();
    await vi.advanceTimersByTimeAsync(3000);
    expect(getRoomLogMock).toHaveBeenCalledOnce();
    expect(getRoomLogMock).toHaveBeenCalledWith("r1");
    w.unmount();
    await vi.advanceTimersByTimeAsync(6000);
    expect(getRoomLogMock).toHaveBeenCalledOnce();
  } finally {
    vi.useRealTimers();
  }
});
