// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { expect, it } from "vitest";
import { humanStateOf, projectExchanges } from "./exchanges.js";
import { entry, turn } from "./test-fixtures/transcript.js";

it("projects consecutive anchors, shared openers, child turns and local pending items", () => {
  const entries = [
    entry(1, { author: "system", author_type: "agent", message_id: null }),
    entry(2),
    entry(3),
    entry(4, { author_type: "agent", targets: ["child"] }),
  ];
  const turns = [
    turn("open", { opener_entry_id: "entry-2", opener_message_id: "message-2" }),
    turn("open", { turn_id: "t2", opener_entry_id: null, opener_message_id: "message-2" }),
    turn("open", { turn_id: "t3", parent_turn_id: "t", opener_entry_id: "entry-4" }),
    turn("open", {
      turn_id: "no-anchor",
      opener_entry_id: "missing",
      opener_message_id: "missing",
    }),
  ];
  const exchanges = projectExchanges(
    entries,
    turns,
    [{ clientSeq: 2 }, { clientSeq: 1 }],
    new Set(["child"]),
  );
  expect(exchanges.map(({ anchorId, entryIds, turnIds }) => [anchorId, entryIds, turnIds])).toEqual(
    [
      ["", ["entry-1"], []],
      ["entry-2", ["entry-2"], ["t", "t2"]],
      ["entry-3", ["entry-3"], []],
      ["entry-4", ["entry-4"], ["t3"]],
      ["pending:1", ["pending:1"], []],
      ["pending:2", ["pending:2"], []],
    ],
  );
  expect(exchanges.map(({ optimistic }) => optimistic)).toEqual([
    false,
    false,
    false,
    false,
    true,
    true,
  ]);
});

it.each(["sent", "queued", "injected", "failed"] as const)(
  "keeps the authoritative human state %s",
  (humanState) => {
    expect(humanStateOf(entry(1, { human_state: humanState }))).toBe(humanState);
  },
);

it("uses pending for optimistic entries and null for unavailable human states", () => {
  expect(humanStateOf(entry(1, { human_state: "failed" }), true)).toBe("pending");
  expect(humanStateOf(entry(2))).toBeNull();
  expect(humanStateOf(entry(3, { human_state: "unknown" }))).toBeNull();
  expect(humanStateOf(entry(4, { author_type: "agent", human_state: "sent" }))).toBeNull();
});
