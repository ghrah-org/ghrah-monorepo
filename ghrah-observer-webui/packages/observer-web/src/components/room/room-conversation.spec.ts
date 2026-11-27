// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom

import {
  useChatStore,
  useHitlStore,
  useRoomsStore,
  useTranscriptStore,
} from "@ghrah/observer-core";
import { ActionNodeSchema, HITLRequestPayloadSchema, RoomInfoPayloadSchema } from "@ghrah/protocol";
import type { TranscriptEntryView, TurnCardView } from "@ghrah/protocol";
import { enableAutoUnmount, flushPromises, mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { setRoomAlign } from "@/composables/useRoomAlign";
import { delivery, entry, turn } from "@/test/turn-card-fixtures";
import RoomConversation from "./room-conversation.vue";

const { loadTurnDetail } = vi.hoisted(() => ({ loadTurnDetail: vi.fn() }));
vi.mock("@/composables/useObserver", () => ({ useObserver: () => ({ loadTurnDetail }) }));
const target = { projectId: "project", roomId: "room" };

enableAutoUnmount(afterEach);
beforeEach(() => {
  setActivePinia(createPinia());
  loadTurnDetail.mockReset();
  sessionStorage.clear();
  setRoomAlign("self-right");
  useChatStore().setCurrentAuthor("Ada");
  useRoomsStore().onRoomCreated({
    room: RoomInfoPayloadSchema.parse({
      room_id: target.roomId,
      project_id: target.projectId,
      name: "Room",
      members: [
        { subject: "agent-1", subject_type: "agent", subject_name: "Coder", joined_at: "" },
      ],
    }),
  });
});

function hydrate(entries: TranscriptEntryView[], turns: TurnCardView[]) {
  const store = useTranscriptStore();
  const state = store.enterRoom(target);
  store.applyPage(
    state,
    {
      project_id: target.projectId,
      room_id: target.roomId,
      entries,
      turns,
      next_before_seq: null,
      next_since_seq: null,
      latest_seq: entries.length,
      has_more: false,
      read_at: "",
    },
    "initial",
  );
  return { store, state };
}

it("groups replies and inserts under the transcript anchor and lazily reads a closed turn once", async () => {
  const closed = turn({ status: "closed", closed_at: "2026-11-26T02:00:06Z" });
  const inserted = entry({
    entry_id: "insert",
    seq: 2,
    content: "Second instruction.",
    human_state: "injected",
    insert_before_turn_id: closed.turn_id,
    injection_step: 1,
    deliveries: [delivery({ injection_step: 1, display_state: "consumed" })],
  });
  const reply = entry({
    entry_id: "reply",
    seq: 3,
    author_type: "agent",
    author: "agent-1",
    turn_id: closed.turn_id,
    ability: "end_task",
    content: "**Result**\nDetailed reply.",
  });
  const { store, state } = hydrate([entry(), inserted, reply], [closed]);
  const nodes = [ActionNodeSchema.parse({ id: "n1", ability_names: ["read_file"] })];
  loadTurnDetail.mockImplementation(async () => {
    store.setTurnDetail(
      state,
      { projectId: target.projectId, agentId: closed.agent_name, turnId: closed.turn_id },
      { turn: closed, nodes },
    );
  });
  const wrapper = mount(RoomConversation, { props: target });
  expect(
    wrapper.findAll("[data-room-item]").map((item) => item.attributes("data-entry-id")),
  ).toEqual(["entry-1", "insert", undefined]);
  expect(wrapper.get(".entry-human--right").element.firstElementChild?.className).toContain(
    "entry-avatar",
  );
  expect(wrapper.find(".entry-human--right .bubble").exists()).toBe(true);
  expect(wrapper.get(".tc-last").text()).toContain("Result");
  expect(loadTurnDetail).not.toHaveBeenCalled();

  const toggle = wrapper.get(".tc-toggle");
  await toggle.trigger("click");
  await flushPromises();
  expect(wrapper.get(".tc-timeline .node + .insert-note--inline").text()).toContain("step 1");
  expect(wrapper.get(".tc-reply strong").text()).toBe("Result");
  expect(loadTurnDetail).toHaveBeenCalledExactlyOnceWith(target.roomId, closed.turn_id);
  await toggle.trigger("click");
  await toggle.trigger("click");
  expect(loadTurnDetail).toHaveBeenCalledOnce();

  setRoomAlign("left-all");
  await wrapper.vm.$nextTick();
  expect(wrapper.find(".bubble").exists()).toBe(false);
});

it("shows the optimistic row and a loading skeleton in an empty room", () => {
  useChatStore().addPendingEntry({
    ...target,
    to: target.roomId,
    agentName: "",
    content: "Sending now",
  });
  const wrapper = mount(RoomConversation, { props: target });
  expect(wrapper.get("[data-pending]").text()).toContain("Sending now");
  expect(wrapper.find(".room-skeleton").exists()).toBe(true);
});

it("only renders pending waits for the same project, agent, and turn", () => {
  const waiting = turn({ status: "waiting_hitl" });
  hydrate([entry()], [waiting]);
  const hitl = useHitlStore();
  for (const [promiseId, projectId, turnId] of [
    ["match", target.projectId, waiting.turn_id],
    ["other-turn", target.projectId, "other"],
    ["other-project", "other", waiting.turn_id],
  ]) {
    hitl.onHitlRequest(
      HITLRequestPayloadSchema.parse({
        project_id: projectId,
        agent_id: waiting.agent_name,
        agent_name: "Coder",
        turn_id: turnId,
        promise_id: promiseId,
        ability_name: "review",
        reason: "Please review.",
      }),
    );
  }
  const wrapper = mount(RoomConversation, { props: target });
  expect(wrapper.get(".turn-card").attributes("data-expanded")).toBe("true");
  expect(wrapper.findAll(".tc-wait").map((row) => row.attributes("data-request-id"))).toEqual([
    "match",
  ]);
  expect(wrapper.get(".tc-wait").attributes("data-reply-kind")).toBe("object");
  expect(wrapper.find(".tc-wait input").exists()).toBe(false);
});
