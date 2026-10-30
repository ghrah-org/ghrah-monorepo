import {
  AbilityTreeViewSchema,
  TreeChangeResultSchema,
  type AbilityTreeView,
  type TreeChangeResult,
} from "@ghrah/protocol";

/** 预先声明结果的切片替身；不另实现授权或工具选择规则。 */
export function createAbilityTreeFixture(initial: AbilityTreeView, responses: TreeChangeResult[]) {
  let current = AbilityTreeViewSchema.parse(structuredClone(initial));
  const queue = responses.map((item) => TreeChangeResultSchema.parse(structuredClone(item)));
  return {
    async read(path: string) {
      if (path !== `/scheme/view/${current.agent_id}`) throw new Error("Fixture target mismatch");
      return structuredClone(current);
    },
    async submit(
      path: string,
      action: "bind_tool" | "unbind_tool",
      arguments_: unknown,
      requestKey: string,
    ) {
      void arguments_;
      if (
        path !== `/scheme/agent/${current.agent_id}` ||
        !requestKey ||
        !["bind_tool", "unbind_tool"].includes(action)
      ) {
        throw new Error("Fixture command mismatch");
      }
      const result = queue.shift();
      if (!result) throw new Error("No declared fixture response");
      if (result.view) current = structuredClone(result.view);
      return structuredClone(result);
    },
  };
}
