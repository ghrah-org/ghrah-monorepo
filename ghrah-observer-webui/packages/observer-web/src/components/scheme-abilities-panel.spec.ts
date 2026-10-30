// @vitest-environment happy-dom
import { createAbilityTreeClient } from "@ghrah/observer-core";
import { AbilityTreeViewSchema, TreeChangeResultSchema } from "@ghrah/protocol";
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
    expect(wrapper.find("code").text()).toBe("read_report");
  });
  it("does not invent state or retry on conflict", async () => {
    const { wrapper, submit } = panel([TreeChangeResultSchema.parse(fixture.conflict)]);
    await flushPromises();
    await wrapper.find("select").setValue("files/read_file");
    await wrapper.find("input").setValue("read_report");
    await wrapper.find("form").trigger("submit");
    await flushPromises();
    expect(wrapper.find('[role="alert"]').text()).toContain("changed");
    expect(wrapper.findAll("code")).toHaveLength(0);
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
    await wrapper.find("li button").trigger("click");
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
    expect(wrapper.findAll("li")).toHaveLength(0);
  });
});
