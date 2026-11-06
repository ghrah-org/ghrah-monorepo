// @vitest-environment happy-dom

import { beforeEach, describe, expect, it, vi } from "vitest";

const ROOM_KEY = "ghrah.workspace.room-sidebar.collapsed";
const CONTEXT_KEY = "ghrah.workspace.context-sidebar.collapsed";

async function loadModule() {
  vi.resetModules();
  return await import("./useSidebarLayout");
}

describe("useSidebarLayout", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it("defaults both sidebars to expanded when storage is empty", async () => {
    const { useSidebarLayout } = await loadModule();
    const { roomSidebarCollapsed, contextSidebarCollapsed } = useSidebarLayout();
    expect(roomSidebarCollapsed.value).toBe(false);
    expect(contextSidebarCollapsed.value).toBe(false);
  });

  it("restores collapsed state from storage on first use", async () => {
    localStorage.setItem(ROOM_KEY, "1");
    localStorage.setItem(CONTEXT_KEY, "1");
    const { useSidebarLayout } = await loadModule();
    const { roomSidebarCollapsed, contextSidebarCollapsed } = useSidebarLayout();
    expect(roomSidebarCollapsed.value).toBe(true);
    expect(contextSidebarCollapsed.value).toBe(true);
  });

  it("toggles each sidebar independently and persists the value", async () => {
    const { useSidebarLayout, toggleRoomSidebar, toggleContextSidebar } = await loadModule();
    const { roomSidebarCollapsed, contextSidebarCollapsed } = useSidebarLayout();

    toggleRoomSidebar();
    expect(roomSidebarCollapsed.value).toBe(true);
    expect(contextSidebarCollapsed.value).toBe(false);
    expect(localStorage.getItem(ROOM_KEY)).toBe("1");
    expect(localStorage.getItem(CONTEXT_KEY)).toBeNull();

    toggleContextSidebar();
    expect(contextSidebarCollapsed.value).toBe(true);
    expect(localStorage.getItem(CONTEXT_KEY)).toBe("1");

    toggleRoomSidebar();
    expect(roomSidebarCollapsed.value).toBe(false);
    expect(localStorage.getItem(ROOM_KEY)).toBe("0");
    expect(contextSidebarCollapsed.value).toBe(true);
  });

  it.each(["0", "true", "yes", "", " 1 "])("treats storage value %j as expanded", async (value) => {
    localStorage.setItem(ROOM_KEY, value);
    const { useSidebarLayout } = await loadModule();
    expect(useSidebarLayout().roomSidebarCollapsed.value).toBe(false);
  });

  it("stays expanded and silent when storage access throws", async () => {
    vi.stubGlobal("localStorage", {
      getItem: () => {
        throw new Error("SecurityError");
      },
      setItem: () => {
        throw new Error("SecurityError");
      },
    });
    const { useSidebarLayout, toggleRoomSidebar } = await loadModule();
    const { roomSidebarCollapsed } = useSidebarLayout();
    expect(roomSidebarCollapsed.value).toBe(false);
    expect(() => toggleRoomSidebar()).not.toThrow();
    expect(roomSidebarCollapsed.value).toBe(true);
  });
});
