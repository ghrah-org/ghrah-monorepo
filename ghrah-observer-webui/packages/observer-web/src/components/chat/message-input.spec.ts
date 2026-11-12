// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import type { RoomMember } from "@ghrah/protocol";
import { flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import MessageInput from "./message-input.vue";

function memberList(agents: string[]): RoomMember[] {
  return [
    ...agents.map((a) => ({
      subject: a,
      subject_type: "agent" as const,
      subject_name: a,
      joined_at: "",
    })),
    { subject: "human:user", subject_type: "human" as const, subject_name: "", joined_at: "" },
  ];
}

const DEFAULT_MEMBERS = memberList(["frontend", "backend"]);

function mountInput(overrides: { members?: RoomMember[]; disabled?: boolean } = {}) {
  return mount(MessageInput, {
    props: {
      members: overrides.members ?? DEFAULT_MEMBERS,
      disabled: overrides.disabled ?? false,
    },
  });
}

describe("MessageInput", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("broadcast: emits empty targets with content", async () => {
    const wrapper = mountInput();
    await wrapper.find("textarea").setValue("hello room");
    await wrapper.find("form").trigger("submit");
    const sendEvents = wrapper.emitted("send");
    expect(sendEvents).toBeTruthy();
    expect(sendEvents![0]).toEqual([[], "hello room", []]);
  });

  it("chip 多选：emits selected targets", async () => {
    const wrapper = mountInput();
    const chips = wrapper
      .findAll('button[type="button"]')
      .filter((button) => button.text().startsWith("@"));
    expect(chips.map((c) => c.text())).toEqual(["@frontend", "@backend"]);
    await chips[0].trigger("click");
    await wrapper.find("textarea").setValue("hi");
    await wrapper.find("form").trigger("submit");
    expect(wrapper.emitted("send")![0]).toEqual([["frontend"], "hi", []]);
  });

  it("@前导解析：targets 合并、content 剥离 mention", async () => {
    const wrapper = mountInput();
    await wrapper.find("textarea").setValue("@frontend 看一下这个");
    await wrapper.find("form").trigger("submit");
    expect(wrapper.emitted("send")![0]).toEqual([["frontend"], "看一下这个", []]);
  });

  it("@补全下拉：候选来自 room 成员，点选并入 targets", async () => {
    const wrapper = mountInput();
    await wrapper.find("textarea").setValue("@front");
    const items = wrapper.findAll("ul li");
    expect(items.map((i) => i.text())).toEqual(["@frontend"]);
    await items[0].trigger("mousedown");
    await wrapper.find("textarea").setValue("go");
    await wrapper.find("form").trigger("submit");
    expect(wrapper.emitted("send")![0]).toEqual([["frontend"], "go", []]);
  });

  it("未知 @name 不算 target，content 保留原文", async () => {
    const wrapper = mountInput();
    await wrapper.find("textarea").setValue("@ghost hello");
    await wrapper.find("form").trigger("submit");
    expect(wrapper.emitted("send")![0]).toEqual([[], "@ghost hello", []]);
  });

  it("clears input and targets after submit", async () => {
    const wrapper = mountInput();
    const chips = wrapper.findAll('button[type="button"]');
    await chips[0].trigger("click");
    const input = wrapper.find("textarea");
    await input.setValue("hi");
    await wrapper.find("form").trigger("submit");
    expect((input.element as HTMLTextAreaElement).value).toBe("");
    expect(wrapper.findAll('button[type="button"]')[0].classes()).not.toContain("bg-blue-100");
  });

  it("does not emit on empty content", async () => {
    const wrapper = mountInput();
    await wrapper.find("textarea").setValue("   ");
    await wrapper.find("form").trigger("submit");
    expect(wrapper.emitted("send")).toBeFalsy();
  });

  it("disables input and button when disabled", () => {
    const wrapper = mountInput({ disabled: true });
    expect(wrapper.find("textarea").attributes("disabled")).toBeDefined();
    expect(wrapper.find('button[type="submit"]').attributes("disabled")).toBeDefined();
  });

  it("无成员时不渲染 target chips（附件按钮仍保留）", () => {
    const wrapper = mountInput({ members: memberList([]) });
    const chips = wrapper
      .findAll('button[type="button"]')
      .filter((button) => button.text().startsWith("@"));
    expect(chips).toHaveLength(0);
  });

  it("renders member names but emits stable IDs for chips and mentions", async () => {
    const wrapper = mountInput({
      members: [
        {
          subject: "stable-shuoxi",
          subject_type: "agent",
          subject_name: "shuoxi",
          joined_at: "",
        },
      ],
    });
    expect(wrapper.find('button[type="button"]').text()).toBe("@shuoxi");
    await wrapper.find("textarea").setValue("@shuoxi 下午好");
    await wrapper.find("form").trigger("submit");
    expect(wrapper.emitted("send")![0]).toEqual([["stable-shuoxi"], "下午好", []]);
  });

  it("Enter 触发发送，Shift+Enter 保留换行不发送", async () => {
    const wrapper = mountInput();
    const textarea = wrapper.find("textarea");
    await textarea.setValue("first line");
    await textarea.trigger("keydown", { key: "Enter" });
    expect(wrapper.emitted("send")![0]).toEqual([[], "first line", []]);

    await textarea.setValue("first line\nsecond");
    await textarea.trigger("keydown", { key: "Enter", shiftKey: true });
    expect(wrapper.emitted("send")).toHaveLength(1);
    expect((textarea.element as HTMLTextAreaElement).value).toBe("first line\nsecond");
  });

  it("Ctrl/Cmd+Enter 触发发送（保持旧快捷键）", async () => {
    const wrapper = mountInput();
    const textarea = wrapper.find("textarea");
    await textarea.setValue("with modifier");
    await textarea.trigger("keydown", { key: "Enter", ctrlKey: true });
    expect(wrapper.emitted("send")![0]).toEqual([[], "with modifier", []]);
    await textarea.setValue("with meta");
    await textarea.trigger("keydown", { key: "Enter", metaKey: true });
    expect(wrapper.emitted("send")).toHaveLength(2);
  });

  it("IME 组合期 Enter 不触发发送", async () => {
    const wrapper = mountInput();
    const textarea = wrapper.find("textarea");
    await textarea.setValue("nihongo");
    await textarea.trigger("keydown", { key: "Enter", isComposing: true });
    expect(wrapper.emitted("send")).toBeFalsy();
  });

  it("renders key hint below input", () => {
    const wrapper = mountInput();
    expect(wrapper.text()).toContain("Enter to send · Shift+Enter for newline");
  });

  function pngFile(name = "shot.png"): File {
    const binary = Uint8Array.from(atob("aGk="), (char) => char.charCodeAt(0));
    return new File([binary], name, { type: "image/png" });
  }

  function selectFiles(wrapper: ReturnType<typeof mountInput>, files: File[]) {
    const element = wrapper.find('input[type="file"]').element as HTMLInputElement;
    Object.defineProperty(element, "files", { configurable: true, value: files });
    return wrapper.find('input[type="file"]').trigger("change");
  }

  it("选择图片后显示缩略图 chip，允许只有块没有文字", async () => {
    const wrapper = mountInput();
    await selectFiles(wrapper, [pngFile()]);
    await vi.waitFor(() => expect(wrapper.find(".attachment-chip img").exists()).toBe(true));
    await wrapper.find("form").trigger("submit");
    expect(wrapper.emitted("send")![0]).toEqual([
      [],
      "",
      [{ type: "image", base64: "aGk=", mime_type: "image/png" }],
    ]);
    expect(wrapper.find(".attachment-chip").exists()).toBe(false);
  });

  it("粘贴图片进入附件；纯文本粘贴不拦截", async () => {
    const wrapper = mountInput();
    const textarea = wrapper.find("textarea");
    const pasteWithFile = new Event("paste", { bubbles: true, cancelable: true });
    Object.defineProperty(pasteWithFile, "clipboardData", {
      value: { files: [pngFile()], getData: () => "" },
    });
    textarea.element.dispatchEvent(pasteWithFile);
    await vi.waitFor(() => expect(wrapper.find(".attachment-chip").exists()).toBe(true));

    const pasteText = new Event("paste", { bubbles: true, cancelable: true });
    Object.defineProperty(pasteText, "clipboardData", {
      value: { files: [], getData: () => "plain text" },
    });
    textarea.element.dispatchEvent(pasteText);
    await flushPromises();
    expect(wrapper.findAll(".attachment-chip")).toHaveLength(1);
  });

  it("拖拽图片进入附件", async () => {
    const wrapper = mountInput();
    const form = wrapper.find("form");
    const drop = new Event("drop", { bubbles: true, cancelable: true });
    Object.defineProperty(drop, "dataTransfer", {
      value: { files: [pngFile()], types: ["Files"] },
    });
    form.element.dispatchEvent(drop);
    await vi.waitFor(() => expect(wrapper.find(".attachment-chip").exists()).toBe(true));
  });

  it("不支持的附件被拒绝并提示，纯文本仍可发送 blocks=[]", async () => {
    const wrapper = mountInput();
    const text = new File(["hello"], "note.txt", { type: "text/plain" });
    await selectFiles(wrapper, [text]);
    await vi.waitFor(() => expect(wrapper.text()).toContain("Unsupported image type"));
    expect(wrapper.find(".attachment-chip").exists()).toBe(false);
    await wrapper.find("textarea").setValue("still works");
    await wrapper.find("form").trigger("submit");
    expect(wrapper.emitted("send")![0]).toEqual([[], "still works", []]);
  });

  it("文字 + 图片：正文与块同时 emit", async () => {
    const wrapper = mountInput();
    await selectFiles(wrapper, [pngFile()]);
    await vi.waitFor(() => expect(wrapper.find(".attachment-chip").exists()).toBe(true));
    await wrapper.find("textarea").setValue("看这张图");
    await wrapper.find("form").trigger("submit");
    expect(wrapper.emitted("send")![0]).toEqual([
      [],
      "看这张图",
      [{ type: "image", base64: "aGk=", mime_type: "image/png" }],
    ]);
  });
});
