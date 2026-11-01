// @vitest-environment happy-dom
import { createAbilityTreeClient } from "@ghrah/observer-core";
import {
  AbilityTreeViewSchema,
  ContentReadResultSchema,
  TreeChangeResultSchema,
} from "@ghrah/protocol";
import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import { createAbilityTreeFixture } from "@ghrah/mock-server";
import fixture from "../../../protocol/src/__fixtures__/ability-tree.json";
import SchemeAbilitiesPanel from "./scheme-abilities-panel.vue";

function panel(
  responses = [TreeChangeResultSchema.parse(fixture.bound)],
  initial: unknown = fixture.initial,
) {
  const transport = createAbilityTreeFixture(AbilityTreeViewSchema.parse(initial), responses);
  const submit = vi.spyOn(transport, "submit");
  const wrapper = mount(SchemeAbilitiesPanel, {
    props: {
      agentId: "alice",
      client: createAbilityTreeClient("project", transport),
    },
  });
  return { wrapper, submit };
}

describe("SchemeAbilitiesPanel", () => {
  it("selects from grants, submits the observed revision, and displays authority responses", async () => {
    const { wrapper, submit } = panel();
    await flushPromises();
    expect(wrapper.findAll("option")).toHaveLength(3);
    expect(wrapper.text()).toContain("No tools selected");
    await wrapper.find("select").setValue("files/read_file");
    await wrapper.find("input").setValue("read_report");
    await wrapper.find("form").trigger("submit");
    await flushPromises();
    expect(submit).toHaveBeenCalledWith(
      "/scheme/agent/alice",
      "bind_tool",
      {
        alias: "files",
        member: "read_file",
        tool_name: "read_report",
        expected_revision: 1,
      },
      expect.any(String),
    );
    expect(wrapper.find(".bound-tools code").text()).toBe("read_report");
  });
  it("does not invent state or retry on conflict", async () => {
    const { wrapper, submit } = panel([TreeChangeResultSchema.parse(fixture.conflict)]);
    await flushPromises();
    await wrapper.find("select").setValue("files/read_file");
    await wrapper.find("input").setValue("read_report");
    await wrapper.find("form").trigger("submit");
    await flushPromises();
    expect(wrapper.find('[role="alert"]').text()).toContain("changed");
    expect(wrapper.findAll(".bound-tools code")).toHaveLength(0);
    expect(submit).toHaveBeenCalledTimes(1);
  });
  it("shows empty and unavailable projections without enabling an invalid binding", async () => {
    const empty = panel([], fixture.revoked).wrapper;
    const unavailable = panel([], fixture.unavailable).wrapper;
    await flushPromises();
    expect(empty.text()).toContain("No granted operations");
    expect(unavailable.text()).toContain("unavailable");
    expect(unavailable.find('[type="submit"]').attributes("disabled")).toBeDefined();
  });
  it("unbinds using the selected relation revision", async () => {
    const { wrapper, submit } = panel(
      [TreeChangeResultSchema.parse({ view: { ...fixture.initial, revision: 3 }, error: null })],
      fixture.selected,
    );
    await flushPromises();
    await wrapper.find(".bound-tools li button").trigger("click");
    await flushPromises();
    expect(submit).toHaveBeenCalledWith(
      "/scheme/agent/alice",
      "unbind_tool",
      {
        tool_name: "read_report",
        expected_revision: 2,
      },
      expect.any(String),
    );
    expect(wrapper.findAll(".bound-tools li")).toHaveLength(0);
  });
});

it("reads granted Skill content through the trusted client and shows binary ranges", async () => {
  const transport = createAbilityTreeFixture(
    AbilityTreeViewSchema.parse(fixture.mixed),
    [],
    [ContentReadResultSchema.parse(fixture.text), ContentReadResultSchema.parse(fixture.binary)],
  );
  const submit = vi.spyOn(transport, "submit");
  const wrapper = mount(SchemeAbilitiesPanel, {
    props: {
      agentId: "alice",
      client: createAbilityTreeClient("project", transport),
    },
  });
  await flushPromises();
  expect(wrapper.text()).toContain("reporting-guide");
  const body = wrapper.findAll("button").find((item) => item.text() === "Read Skill body")!;
  await body.trigger("click");
  await flushPromises();
  expect(wrapper.find("pre").text()).toContain("# 报告编写");
  expect(submit).toHaveBeenCalledWith(
    "/scheme/agent/alice/ability/reports/skill/guide",
    "read",
    { offset: 0, limit: 65536 },
    expect.any(String),
  );
  const asset = wrapper.findAll("li").find((item) => item.find("code").text() === "assets/mark")!;
  await asset.find("button").trigger("click");
  await flushPromises();
  expect(wrapper.text()).toContain("Binary content");
  expect(wrapper.find("pre").exists()).toBe(false);
  expect(wrapper.text()).toContain("Bytes 0 + 4 of 4");
});
