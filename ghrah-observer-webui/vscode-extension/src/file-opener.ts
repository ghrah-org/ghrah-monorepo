// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import * as os from "node:os";
import * as path from "node:path";
import { Uri, window, workspace } from "vscode";

export function handleWebviewMessage(msg: unknown, manifestRoot: string): void {
  if (typeof msg !== "object" || msg === null) return;

  const { type, fullName, kind } = msg as Record<string, unknown>;

  if (type !== "openFile" || typeof fullName !== "string") return;

  const expanded = manifestRoot.startsWith("~")
    ? path.join(os.homedir(), manifestRoot.slice(1))
    : manifestRoot;

  let filePath: string;
  if (kind === "ability") {
    const parts = fullName.split(".");
    const namespace = parts.slice(0, -1).join("/") || parts[0];
    const name = parts[parts.length - 1];
    filePath = path.join(expanded, "abilities", namespace, `${name}.yaml`);
  } else {
    const parts = fullName.split(".");
    const namespace = parts.slice(0, -1).join("/") || parts[0];
    const name = parts[parts.length - 1];
    filePath = path.join(expanded, "agents", namespace, `${name}.yaml`);
  }

  const uri = Uri.file(filePath);
  workspace.openTextDocument(uri).then(
    (doc) => window.showTextDocument(doc),
    () => window.showErrorMessage(`Failed to open manifest file: ${filePath}`),
  );
}
