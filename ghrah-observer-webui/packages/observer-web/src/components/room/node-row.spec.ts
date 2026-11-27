// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

// @vitest-environment happy-dom
import { ActionNodeSchema } from "@ghrah/protocol";
import { mount } from "@vue/test-utils";
import { expect, it } from "vitest";
import NodeRow from "./node-row.vue";

it("shows the compact metrics and independent warnings with a collapsed attributed snapshot", () => {
  const wrapper = mount(NodeRow, {
    props: {
      projectId: "project",
      node: ActionNodeSchema.parse({
        id: "compact",
        is_snapshot: true,
        metadata: {
          tokens_before: 1000,
          tokens_after: 200,
          post_check: "over_budget",
          degraded: true,
          truncated_summary_input: true,
          summarized_range: ["first", "last"],
          kept_recent_nodes: 2,
        },
        messages_snapshot: [
          {
            role: "ai",
            source: "summary",
            content_blocks: [{ type: "text", text: "Snapshot body" }],
          },
        ],
      }),
    },
  });
  expect(wrapper.classes()).toContain("node--compact");
  expect(wrapper.text()).toContain("1000 → 200 tok");
  expect(wrapper.findAll(".node-error")).toHaveLength(3);
  const details = wrapper.get("details");
  expect(details.element.open).toBe(false);
  expect(details.get("summary").attributes("aria-controls")).toBe(
    details.get(".node-detail").attributes("id"),
  );
  expect(details.get(".node-detail").text()).toContain("ai · summary");
  expect(details.get(".node-detail").text()).toContain("Snapshot body");
});
