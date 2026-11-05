// SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
//
// SPDX-License-Identifier: Apache-2.0

import MarkdownIt from "markdown-it";

const BASE_URL = new URL("https://chat.ghrah.invalid/");
const ALLOWED_PROTOCOLS = new Set(["http:", "https:", "mailto:", "tel:"]);

function validateLink(rawUrl: string): boolean {
  try {
    const parsed = new URL(rawUrl.trim(), BASE_URL);
    return ALLOWED_PROTOCOLS.has(parsed.protocol);
  } catch {
    return false;
  }
}

const md = new MarkdownIt({
  html: false,
  linkify: true,
  // 聊天场景唯一消费方是 chat-panel：多行消息的单换行语义必须保留（转 <br>）；
  // 列表/代码块等块级语法不受影响。
  breaks: true,
});
md.validateLink = validateLink;

export function useMarkdown() {
  const render = (src: string): string => md.render(src ?? "");
  return { render };
}

export { validateLink };
