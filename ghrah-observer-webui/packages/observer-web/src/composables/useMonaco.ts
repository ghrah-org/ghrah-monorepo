// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

export function useMonaco() {
  async function createDiffEditor(
    container: HTMLElement,
    original: string,
    modified: string,
    language = "typescript",
  ) {
    const monaco = await import("monaco-editor");
    const diffEditor = monaco.editor.createDiffEditor(container, {
      readOnly: true,
      renderSideBySide: true,
      fontSize: 13,
    });
    diffEditor.setModel({
      original: monaco.editor.createModel(original, language),
      modified: monaco.editor.createModel(modified, language),
    });
    return diffEditor;
  }

  return { createDiffEditor };
}
