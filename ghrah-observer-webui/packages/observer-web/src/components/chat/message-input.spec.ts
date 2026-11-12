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

function mountInput(
  overrides: {
    members?: RoomMember[];
    disabled?: boolean;
    attachmentDeps?: import("@/composables/useAttachments").AttachmentDeps;
  } = {},
) {
  return mount(MessageInput, {
    props: {
      members: overrides.members ?? DEFAULT_MEMBERS,
      disabled: overrides.disabled ?? false,
      projectId: "p1",
      ...(overrides.attachmentDeps ? { attachmentDeps: overrides.attachmentDeps } : {}),
    },
  });
}

async function openTargets(wrapper: ReturnType<typeof mountInput>) {
  await wrapper.find(".chat-target-trigger").trigger("click");
}

function targetOptions(wrapper: ReturnType<typeof mountInput>) {
  return wrapper.findAll(".chat-target-option");
}

async function toggleOption(wrapper: ReturnType<typeof mountInput>, name: string) {
  const option = targetOptions(wrapper).find((item) => item.text().includes(name));
  await option!.find('input[type="checkbox"]').trigger("change");
}

function targetButton(wrapper: ReturnType<typeof mountInput>) {
  return wrapper.find(".chat-target-trigger");
}

describe("MessageInput", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it("默认选中第一个 agent，发送 targets 非空", async () => {
    const wrapper = mountInput();
    expect(targetButton(wrapper).text()).toContain("@frontend");
    await wrapper.find("textarea").setValue("hello room");
    await wrapper.find("form").trigger("submit");
    expect(wrapper.emitted("send")![0]).toEqual([["frontend"], "hello room", []]);
  });

  it("不再渲染底部快捷键说明行", () => {
    const wrapper = mountInput();
    expect(wrapper.text()).not.toContain("Enter to send");
    expect(wrapper.find(".chat-hint").exists()).toBe(false);
  });

  it("附件入口是图标按钮，aria-label 保留且可点击", async () => {
    const wrapper = mountInput();
    const button = wrapper.find('button[aria-label="Add attachment"]');
    expect(button.exists()).toBe(true);
    expect(button.find("svg").exists()).toBe(true);
    const click = vi.spyOn(HTMLInputElement.prototype, "click").mockImplementation(() => undefined);
    await button.trigger("click");
    expect(click).toHaveBeenCalledOnce();
    click.mockRestore();
  });

  it("目标下拉：默认第一个 agent、复选列表与全选/取消全选", async () => {
    const wrapper = mountInput();
    await openTargets(wrapper);
    const options = targetOptions(wrapper);
    expect(options.map((item) => item.text())).toEqual(["@frontend", "@backend"]);
    const boxes = wrapper.findAll('.chat-target-option input[type="checkbox"]');
    expect((boxes[0].element as HTMLInputElement).checked).toBe(true);
    expect((boxes[1].element as HTMLInputElement).checked).toBe(false);

    await wrapper.findAll(".chat-target-action")[0].trigger("click");
    expect(targetButton(wrapper).text()).toContain("All");
    expect(
      wrapper
        .findAll('.chat-target-option input[type="checkbox"]')
        .every((box) => (box.element as HTMLInputElement).checked),
    ).toBe(true);

    await wrapper.findAll(".chat-target-action")[1].trigger("click");
    expect(targetButton(wrapper).text()).toContain("@frontend");
  });

  it("目标下拉：多选显示数量，全选显示全部", async () => {
    const wrapper = mountInput({ members: memberList(["a", "b", "c"]) });
    await openTargets(wrapper);
    await toggleOption(wrapper, "b");
    expect(targetButton(wrapper).text()).toContain("2 selected");
    await toggleOption(wrapper, "c");
    expect(targetButton(wrapper).text()).toContain("All");
  });

  it("取消最后一个目标时至少保留一个，避免退回广播", async () => {
    const wrapper = mountInput();
    await openTargets(wrapper);
    await toggleOption(wrapper, "frontend");
    expect(targetButton(wrapper).text()).toContain("@frontend");
    expect(
      (wrapper.findAll('.chat-target-option input[type="checkbox"]')[0].element as HTMLInputElement)
        .checked,
    ).toBe(true);
  });

  it("目标下拉：点击外部与 Esc 关闭", async () => {
    const wrapper = mountInput();
    await openTargets(wrapper);
    expect(wrapper.find(".chat-target-menu").exists()).toBe(true);
    document.dispatchEvent(new Event("pointerdown"));
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".chat-target-menu").exists()).toBe(false);

    await openTargets(wrapper);
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".chat-target-menu").exists()).toBe(false);
  });

  it("@前导解析：与下拉选择合并去重、content 剥离 mention", async () => {
    const wrapper = mountInput();
    await wrapper.find("textarea").setValue("@backend 看一下这个");
    await wrapper.find("form").trigger("submit");
    expect(wrapper.emitted("send")![0]).toEqual([["frontend", "backend"], "看一下这个", []]);

    const second = mountInput();
    await second.find("textarea").setValue("@frontend hi");
    await second.find("form").trigger("submit");
    expect(second.emitted("send")![0]).toEqual([["frontend"], "hi", []]);
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
    expect(wrapper.emitted("send")![0]).toEqual([["frontend"], "@ghost hello", []]);
  });

  it("clears input and resets targets after submit", async () => {
    const wrapper = mountInput();
    const input = wrapper.find("textarea");
    await input.setValue("hi");
    await wrapper.find("form").trigger("submit");
    expect((input.element as HTMLTextAreaElement).value).toBe("");
    expect(targetButton(wrapper).text()).toContain("@frontend");
  });

  it("does not emit on empty content", async () => {
    const wrapper = mountInput();
    await wrapper.find("textarea").setValue("   ");
    await wrapper.find("form").trigger("submit");
    expect(wrapper.emitted("send")).toBeFalsy();
  });

  it("disables input and buttons when disabled", () => {
    const wrapper = mountInput({ disabled: true });
    expect(wrapper.find("textarea").attributes("disabled")).toBeDefined();
    expect(wrapper.find('button[type="submit"]').attributes("disabled")).toBeDefined();
    expect(targetButton(wrapper).attributes("disabled")).toBeDefined();
  });

  it("无 agent 成员时不渲染目标下拉（附件按钮仍保留）", () => {
    const wrapper = mountInput({ members: memberList([]) });
    expect(wrapper.find(".chat-target-trigger").exists()).toBe(false);
    expect(wrapper.find('button[aria-label="Add attachment"]').exists()).toBe(true);
  });

  it("renders member names but emits stable IDs for targets and mentions", async () => {
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
    expect(targetButton(wrapper).text()).toContain("@shuoxi");
    await wrapper.find("textarea").setValue("@shuoxi 下午好");
    await wrapper.find("form").trigger("submit");
    expect(wrapper.emitted("send")![0]).toEqual([["stable-shuoxi"], "下午好", []]);
  });

  it("Enter 触发发送，Shift+Enter 保留换行不发送", async () => {
    const wrapper = mountInput();
    const textarea = wrapper.find("textarea");
    await textarea.setValue("first line");
    await textarea.trigger("keydown", { key: "Enter" });
    expect(wrapper.emitted("send")![0]).toEqual([["frontend"], "first line", []]);

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
    expect(wrapper.emitted("send")![0]).toEqual([["frontend"], "with modifier", []]);
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
      ["frontend"],
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
    const text = new File(["hello"], "icon.svg", { type: "image/svg+xml" });
    await selectFiles(wrapper, [text]);
    await vi.waitFor(() => expect(wrapper.text()).toContain("Unsupported file type"));
    expect(wrapper.find(".attachment-chip").exists()).toBe(false);
    await wrapper.find("textarea").setValue("still works");
    await wrapper.find("form").trigger("submit");
    expect(wrapper.emitted("send")![0]).toEqual([["frontend"], "still works", []]);
  });

  it("文字 + 图片：正文与块同时 emit", async () => {
    const wrapper = mountInput();
    await selectFiles(wrapper, [pngFile()]);
    await vi.waitFor(() => expect(wrapper.find(".attachment-chip").exists()).toBe(true));
    await wrapper.find("textarea").setValue("看这张图");
    await wrapper.find("form").trigger("submit");
    expect(wrapper.emitted("send")![0]).toEqual([
      ["frontend"],
      "看这张图",
      [{ type: "image", base64: "aGk=", mime_type: "image/png" }],
    ]);
  });

  it("accept 扩展为图片、视频与常见文件类型", () => {
    const wrapper = mountInput();
    const accept = wrapper.find('input[type="file"]').attributes("accept") ?? "";
    expect(accept).toContain("image/*");
    expect(accept).toContain("video/*");
    expect(accept).toContain(".pdf");
  });

  it("小文件内联为 file 块并允许随文字发送", async () => {
    const wrapper = mountInput({
      attachmentDeps: {
        readAsDataUrl: async () => "data:text/markdown;base64,ZmlsZQ==",
      },
    });
    await selectFiles(wrapper, [new File(["file"], "notes.md", { type: "text/markdown" })]);
    await vi.waitFor(() => expect(wrapper.find(".attachment-chip").exists()).toBe(true));
    await wrapper.find("textarea").setValue("看这个文件");
    await wrapper.find("form").trigger("submit");
    expect(wrapper.emitted("send")![0]).toEqual([
      ["frontend"],
      "看这个文件",
      [
        {
          type: "file",
          base64: "ZmlsZQ==",
          mime_type: "text/markdown",
          filename: "notes.md",
        },
      ],
    ]);
  });

  it("视频抽帧 + 上传后 emit video 块", async () => {
    const wrapper = mountInput({
      attachmentDeps: {
        probeVideo: async () => ({
          durationS: 5,
          width: 640,
          height: 360,
          frames: [{ type: "image" as const, base64: "RlJBTUU=", mime_type: "image/jpeg" }],
        }),
        upload: async () => ({
          attachmentId: "att-v",
          size: 1024,
          mimeType: "video/mp4",
          filename: "clip.mp4",
        }),
      },
    });
    await selectFiles(wrapper, [
      new File([Uint8Array.from([1])], "clip.mp4", { type: "video/mp4" }),
    ]);
    await vi.waitFor(() => expect(wrapper.find(".attachment-chip").exists()).toBe(true));
    expect(wrapper.find(".attachment-chip img").exists()).toBe(true);
    expect(wrapper.text()).toContain("0:05");
    await wrapper.find("form").trigger("submit");
    expect(wrapper.emitted("send")![0]).toEqual([
      ["frontend"],
      "",
      [
        {
          type: "video",
          attachment_id: "att-v",
          mime_type: "video/mp4",
          filename: "clip.mp4",
          duration_s: 5,
          width: 640,
          height: 360,
          frames: [{ type: "image", base64: "RlJBTUU=", mime_type: "image/jpeg" }],
        },
      ],
    ]);
  });
});
