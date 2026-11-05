// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import {
  commands,
  type ExtensionContext,
  Uri,
  ViewColumn,
  type WebviewPanel,
  window,
  workspace,
} from "vscode";
import { handleWebviewMessage } from "./file-opener.js";
import { Launcher } from "./launcher.js";

let panel: WebviewPanel | undefined;
let launcher: Launcher | undefined;

export async function activate(context: ExtensionContext) {
  launcher = new Launcher();

  const config = workspace.getConfiguration("ghrah");
  const autoStart = config.get<boolean>("core.autoStart", true);

  if (autoStart) {
    const coreUrl = config.get<string>("core.url", "ws://localhost:4111/ws");
    const launchMethod = config.get<string>("core.launchMethod", "uv");
    const binaryPath = config.get<string>("core.binaryPath", "");
    const workspaceRoot = config.get<string>("workspace.root", "");

    await launcher.ensureCore(coreUrl, { launchMethod, binaryPath });

    const subjectAutoStart = config.get<boolean>("subject.autoStart", true);
    if (subjectAutoStart && workspaceRoot) {
      const observerUrl = config.get<string>("observer.url", "ws://localhost:4112/ws");
      await launcher.ensureSubject(observerUrl, workspaceRoot, coreUrl);
    }
  }

  context.subscriptions.push(
    commands.registerCommand("ghrah.openDashboard", () => {
      openDashboard(context);
    }),
    commands.registerCommand("ghrah.openConfig", () => {
      openDashboard(context, "/config");
    }),
    commands.registerCommand("ghrah.startCluster", async () => {
      if (launcher) {
        await launcher.startCluster();
      }
    }),
    commands.registerCommand("ghrah.stopCluster", async () => {
      if (launcher) {
        await launcher.stopCluster();
      }
    }),
  );
}

export function deactivate() {
  panel?.dispose();
  launcher?.stopAll();
}

function openDashboard(context: ExtensionContext, initialPath?: string) {
  if (panel) {
    panel.reveal(ViewColumn.One);
    if (initialPath) {
      panel.webview.postMessage({ type: "navigate", path: initialPath });
    }
    return;
  }

  panel = window.createWebviewPanel("ghrah-dashboard", "Ghrah Dashboard", ViewColumn.One, {
    enableScripts: true,
    retainContextWhenHidden: true,
    localResourceRoots: [Uri.joinPath(context.extensionUri, "dist-web")],
  });

  panel.webview.html = getWebviewContent(context, panel);

  panel.webview.onDidReceiveMessage((msg) => {
    const manifestRoot = workspace
      .getConfiguration("ghrah")
      .get<string>("manifest.root", "~/.ghrah/manifests");
    handleWebviewMessage(msg, manifestRoot);
  });

  panel.onDidDispose(() => {
    panel = undefined;
  });
}

function getWebviewContent(context: ExtensionContext, _panel: WebviewPanel): string {
  const isDev = process.env.GHRAH_DEV === "1";
  const devUrl = "http://localhost:5173";

  if (isDev) {
    return /* html */ `
      <!DOCTYPE html>
      <html lang="en">
      <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Ghrah Dashboard</title>
      </head>
      <body style="margin:0;padding:0;width:100%;height:100vh;">
        <iframe src="${devUrl}" style="width:100%;height:100%;border:none;"></iframe>
      </body>
      </html>
    `;
  }

  const webview = _panel.webview;
  const scriptUri = webview.asWebviewUri(
    Uri.joinPath(context.extensionUri, "dist-web", "assets", "index.js"),
  );
  const styleUri = webview.asWebviewUri(
    Uri.joinPath(context.extensionUri, "dist-web", "assets", "index.css"),
  );
  const cspSource = webview.cspSource;

  return /* html */ `
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src ${cspSource}; style-src ${cspSource} 'unsafe-inline'; connect-src ws://localhost:* wss://localhost:*;">
      <title>Ghrah Dashboard</title>
      <link rel="stylesheet" href="${styleUri}">
    </head>
    <body>
      <div id="app"></div>
      <script src="${scriptUri}"></script>
    </body>
    </html>
  `;
}
