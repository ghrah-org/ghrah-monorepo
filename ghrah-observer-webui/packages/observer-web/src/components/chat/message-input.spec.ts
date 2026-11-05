// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import type { RoomMember } from "@ghrah/protocol";
import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it } from "vitest";
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
    expect(sendEvents![0]).toEqual([[], "hello room"]);
  });

  it("chip 多选：emits selected targets", async () => {
    const wrapper = mountInput();
    const chips = wrapper.findAll('button[type="button"]');
    expect(chips.map((c) => c.text())).toEqual(["@frontend", "@backend"]);
    await chips[0].trigger("click");
    await wrapper.find("textarea").setValue("hi");
    await wrapper.find("form").trigger("submit");
    expect(wrapper.emitted("send")![0]).toEqual([["frontend"], "hi"]);
  });

  it("@前导解析：targets 合并、content 剥离 mention", async () => {
    const wrapper = mountInput();
    await wrapper.find("textarea").setValue("@frontend 看一下这个");
    await wrapper.find("form").trigger("submit");
    expect(wrapper.emitted("send")![0]).toEqual([["frontend"], "看一下这个"]);
  });

  it("@补全下拉：候选来自 room 成员，点选并入 targets", async () => {
    const wrapper = mountInput();
    await wrapper.find("textarea").setValue("@front");
    const items = wrapper.findAll("ul li");
    expect(items.map((i) => i.text())).toEqual(["@frontend"]);
    await items[0].trigger("mousedown");
    await wrapper.find("textarea").setValue("go");
    await wrapper.find("form").trigger("submit");
    expect(wrapper.emitted("send")![0]).toEqual([["frontend"], "go"]);
  });

  it("未知 @name 不算 target，content 保留原文", async () => {
    const wrapper = mountInput();
    await wrapper.find("textarea").setValue("@ghost hello");
    await wrapper.find("form").trigger("submit");
    expect(wrapper.emitted("send")![0]).toEqual([[], "@ghost hello"]);
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

  it("无成员时不渲染 chips", () => {
    const wrapper = mountInput({ members: memberList([]) });
    expect(wrapper.findAll('button[type="button"]')).toHaveLength(0);
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
    expect(wrapper.emitted("send")![0]).toEqual([["stable-shuoxi"], "下午好"]);
  });

  it("Enter 触发发送，Shift+Enter 保留换行不发送", async () => {
    const wrapper = mountInput();
    const textarea = wrapper.find("textarea");
    await textarea.setValue("first line");
    await textarea.trigger("keydown", { key: "Enter" });
    expect(wrapper.emitted("send")![0]).toEqual([[], "first line"]);

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
    expect(wrapper.emitted("send")![0]).toEqual([[], "with modifier"]);
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
});
