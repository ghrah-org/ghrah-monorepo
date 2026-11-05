// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import { demoScenario } from "./demo-4agent-4room.js";
import { pluginsScenario } from "./plugins.js";
import type { Scenario } from "./types.js";

const REGISTRY = new Map<string, Scenario>([
  [demoScenario.name, demoScenario],
  [pluginsScenario.name, pluginsScenario],
]);

export function getScenario(name: string): Scenario {
  const scenario = REGISTRY.get(name);
  if (!scenario) {
    throw new Error(`unknown scenario: ${name} (available: ${[...REGISTRY.keys()].join(", ")})`);
  }
  return scenario;
}

export function listScenarios(): Scenario[] {
  return [...REGISTRY.values()];
}

export { demoScenario } from "./demo-4agent-4room.js";
export { pluginsScenario } from "./plugins.js";
export type { Scenario, ScenarioContext, TimelineAction, TimelineEntry } from "./types.js";
export { runScenario } from "./types.js";
