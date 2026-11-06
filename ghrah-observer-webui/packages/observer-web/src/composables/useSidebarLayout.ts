import { ref } from "vue";

const ROOM_SIDEBAR_KEY = "ghrah.workspace.room-sidebar.collapsed";
const CONTEXT_SIDEBAR_KEY = "ghrah.workspace.context-sidebar.collapsed";

// 「1」表示收起；其余值（含缺失、损坏）一律按展开处理。
const roomSidebarCollapsed = ref(false);
const contextSidebarCollapsed = ref(false);
let initialized = false;

function readCollapsed(key: string): boolean {
  try {
    return globalThis.localStorage?.getItem(key) === "1";
  } catch {
    return false; // 嵌入/隐私上下文可能禁用存储
  }
}

function writeCollapsed(key: string, collapsed: boolean) {
  try {
    globalThis.localStorage?.setItem(key, collapsed ? "1" : "0");
  } catch {
    // 存不下就只保留内存状态，交互不受影响
  }
}

function ensureInitialized() {
  if (initialized) return;
  initialized = true;
  roomSidebarCollapsed.value = readCollapsed(ROOM_SIDEBAR_KEY);
  contextSidebarCollapsed.value = readCollapsed(CONTEXT_SIDEBAR_KEY);
}

export function toggleRoomSidebar() {
  roomSidebarCollapsed.value = !roomSidebarCollapsed.value;
  writeCollapsed(ROOM_SIDEBAR_KEY, roomSidebarCollapsed.value);
}

export function toggleContextSidebar() {
  contextSidebarCollapsed.value = !contextSidebarCollapsed.value;
  writeCollapsed(CONTEXT_SIDEBAR_KEY, contextSidebarCollapsed.value);
}

export function useSidebarLayout() {
  ensureInitialized();
  return {
    roomSidebarCollapsed,
    contextSidebarCollapsed,
    toggleRoomSidebar,
    toggleContextSidebar,
  };
}
