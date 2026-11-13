# SPDX-FileCopyrightText: 2026 chenxya <chenxya@ghrah.org>
#
# SPDX-License-Identifier: Apache-2.0
"""把 ``docs/ARCHITECTURE.md`` 生成为单文件分享页 ``docs/ARCHITECTURE.html``。

设计取舍：

- **单一来源**：HTML 一律由 Markdown 生成，不手工维护第二份内容，避免两者漂移；
- **零依赖**：仓库不为此引入 markdown 库（新增依赖须入 workspace 清单），
  因此这里实现一个只覆盖本仓文档实际用法的子集渲染器；
- **单文件**：CSS / JS 内联，便于直接发给别人打开；不引用外部 CDN；
- **可重跑**：同样输入得到同样输出（无时间戳、无随机量）。

只支持本仓文档用到的 Markdown 子集：标题、段落、列表、表格、围栏代码、
引用块、分隔线，以及行内的 ``code`` / **粗体** / [链接](…)。

用法::

    uv run python scripts/gen_architecture_html.py
"""

from __future__ import annotations

import html
import re
from pathlib import Path

__all__ = ["main", "render"]

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "ARCHITECTURE.md"
TARGET = ROOT / "docs" / "ARCHITECTURE.html"

_HEADING_RE = re.compile(r"^(#{1,4})\s+(.*)$")
_ORDERED_RE = re.compile(r"^\s*(\d+)\.\s+(.*)$")
_BULLET_RE = re.compile(r"^\s*[-*]\s+(.*)$")
_FENCE_RE = re.compile(r"^```(\w*)\s*$")
_SEPARATOR_RE = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|-]*$")
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")

_SLUG_STRIP_RE = re.compile(r"[^\w\u4e00-\u9fff\- ]+")

_CSS = """
:root{
  --bg:#eef1f6; --panel:#fff; --ink:#151b2c; --ink-2:#4b5563; --ink-3:#8b93a1;
  --line:#dfe4ee; --accent:#2b5fd9; --accent-weak:#eaf1fe; --navy:#1b2c6e;
  --code-bg:#f3f4f7; --sidebar:300px; --radius:12px;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--ink);font-size:15px;line-height:1.75;
  font-family:"PingFang SC","Microsoft YaHei","Hiragino Sans GB","Noto Sans CJK SC",
  "Source Han Sans SC","Segoe UI Emoji",system-ui,-apple-system,sans-serif}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
code{font-family:"Consolas","Menlo",ui-monospace,monospace;font-size:12.5px;
  background:var(--code-bg);padding:1.5px 5px;border-radius:5px;color:#1f2937}
.sidebar{position:fixed;inset:0 auto 0 0;width:var(--sidebar);background:var(--panel);
  border-right:1px solid var(--line);display:flex;flex-direction:column;z-index:30;
  transition:transform .22s ease}
body.nav-collapsed .sidebar{transform:translateX(-100%)}
body.nav-collapsed .main{margin-left:0}
.brand{padding:20px;border-bottom:1px solid var(--line)}
.brand .mk{width:30px;height:30px;border-radius:9px;margin-bottom:9px;
  background:linear-gradient(135deg,#1b2c6e 0%,#2b5fd9 100%);
  display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:15px}
.brand h1{margin:0;font-size:15px;line-height:1.4}
.brand p{margin:4px 0 0;font-size:12px;color:var(--ink-3)}
.search{margin:12px 16px 0}
.search input{width:100%;padding:7px 10px;border:1px solid var(--line);border-radius:8px;
  font-size:13px;background:#fbfcfe}
.nav{overflow-y:auto;padding:10px 10px 24px;flex:1}
.nav a{display:block;padding:5px 10px;border-radius:7px;color:var(--ink-2);font-size:13px}
.nav a:hover{background:var(--accent-weak);color:var(--accent);text-decoration:none}
.nav a.lv3{padding-left:24px;font-size:12.5px;color:var(--ink-3)}
.nav a.active{background:var(--accent-weak);color:var(--accent);font-weight:600}
.main{margin-left:var(--sidebar);padding:34px 46px 90px;max-width:1080px}
.toggle{position:fixed;top:14px;left:14px;z-index:40;display:none;border:1px solid var(--line);
  background:var(--panel);border-radius:9px;padding:6px 10px;font-size:13px;cursor:pointer}
h1,h2,h3,h4{line-height:1.35;scroll-margin-top:20px}
h1{font-size:30px;margin:0 0 18px;color:var(--navy)}
h2{font-size:23px;margin:44px 0 14px;padding-bottom:8px;border-bottom:1px solid var(--line)}
h3{font-size:18px;margin:28px 0 10px;color:var(--navy)}
h4{font-size:15.5px;margin:20px 0 8px}
p{margin:10px 0}
ul,ol{margin:10px 0;padding-left:24px}
li{margin:4px 0}
hr{border:0;border-top:1px solid var(--line);margin:34px 0}
blockquote{margin:14px 0;padding:10px 16px;border-left:3px solid var(--accent);
  background:var(--accent-weak);border-radius:0 8px 8px 0;color:var(--ink-2)}
blockquote p{margin:4px 0}
pre{background:#1b2333;color:#e6e9f0;padding:14px 16px;border-radius:var(--radius);
  overflow-x:auto;font-size:12.5px;line-height:1.6;margin:14px 0}
pre code{background:none;color:inherit;padding:0;font-size:inherit}
pre.diagram{background:#f7f9fc;color:var(--ink);border:1px solid var(--line)}
pre.diagram::before{content:"图（Mermaid 源）";display:block;font-size:11.5px;
  color:var(--ink-3);margin-bottom:8px;font-family:inherit}
table{border-collapse:collapse;width:100%;margin:14px 0;font-size:13.5px;
  background:var(--panel);border-radius:var(--radius);overflow:hidden}
th,td{border:1px solid var(--line);padding:8px 11px;text-align:left;vertical-align:top}
th{background:#f6f8fc;font-weight:600;color:var(--navy);white-space:nowrap}
tbody tr:nth-child(even){background:#fbfcfe}
.xref{color:var(--ink-3)}
mark{background:#fff2a8;padding:0 2px;border-radius:3px}
@media (max-width:900px){
  .sidebar{transform:translateX(-100%)}
  .main{margin-left:0;padding:64px 20px 70px}
  .toggle{display:block}
  body.nav-open .sidebar{transform:none}
}
"""

_JS = """
(function(){
  var body=document.body, toggle=document.querySelector('.toggle');
  if(toggle){toggle.addEventListener('click',function(){body.classList.toggle('nav-open');});}
  var input=document.querySelector('.search input');
  var items=[].slice.call(document.querySelectorAll('.nav a'));
  if(input){
    input.addEventListener('input',function(){
      var q=input.value.trim().toLowerCase();
      items.forEach(function(a){
        a.style.display = (!q || a.textContent.toLowerCase().indexOf(q)>=0) ? '' : 'none';
      });
    });
  }
  var heads=[].slice.call(document.querySelectorAll('h2[id],h3[id]'));
  if(heads.length && 'IntersectionObserver' in window){
    var links={};
    items.forEach(function(a){ links[a.getAttribute('href')]=a; });
    var io=new IntersectionObserver(function(entries){
      entries.forEach(function(e){
        if(!e.isIntersecting) return;
        items.forEach(function(a){a.classList.remove('active');});
        var a=links['#'+e.target.id];
        if(a){a.classList.add('active');}
      });
    },{rootMargin:'-15% 0px -75% 0px'});
    heads.forEach(function(h){io.observe(h);});
  }
})();
"""


def _slug(text: str, used: dict[str, int]) -> str:
    """由标题文本生成稳定锚点，重复标题追加序号。

    Args:
        text: 标题原文
        used: 已用锚点计数表（原地更新）

    Returns:
        URL 片段
    """
    plain = _INLINE_CODE_RE.sub(r"\1", text)
    plain = _BOLD_RE.sub(r"\1", plain)
    plain = _LINK_RE.sub(r"\1", plain)
    slug = _SLUG_STRIP_RE.sub("", plain).strip().lower().replace(" ", "-")
    slug = slug or "section"
    if slug in used:
        used[slug] += 1
        return f"{slug}-{used[slug]}"
    used[slug] = 1
    return slug


def _inline(text: str) -> str:
    """渲染行内标记：先转义，再处理 ``code`` / **粗体** / 链接。

    Args:
        text: 行内 Markdown 文本

    Returns:
        安全的 HTML 片段
    """
    placeholders: list[str] = []

    def stash(fragment: str) -> str:
        placeholders.append(fragment)
        return f"\x00{len(placeholders) - 1}\x00"

    def code(match: re.Match[str]) -> str:
        # 整行已做过一次 html.escape，这里不能再转义，否则 " 会变成 &amp;quot;。
        return stash(f"<code>{match.group(1)}</code>")

    def bold(match: re.Match[str]) -> str:
        return stash(f"<strong>{match.group(1)}</strong>")

    def link(match: re.Match[str]) -> str:
        label = match.group(1)
        target = match.group(2)
        if target.startswith(("http://", "https://", "#")):
            return stash(f'<a href="{target}">{label}</a>')
        # 指向仓内其它 Markdown 的链接在单文件页里无法跳转，降为纯文本标注。
        return stash(f'<span class="xref">{label}（{target}）</span>')

    escaped = html.escape(text)
    escaped = _INLINE_CODE_RE.sub(code, escaped)
    escaped = _BOLD_RE.sub(bold, escaped)
    escaped = _LINK_RE.sub(link, escaped)
    for index, fragment in enumerate(placeholders):
        escaped = escaped.replace(f"\x00{index}\x00", fragment)
    return escaped


def _cells(line: str) -> list[str]:
    """按未转义竖线切分表格行。

    Args:
        line: 表格行文本

    Returns:
        单元格文本列表（已剥离首尾竖线）
    """
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    parts = re.split(r"(?<!\\)\|", stripped)
    return [part.strip().replace("\\|", "|") for part in parts]


def render(markdown: str) -> tuple[str, list[tuple[int, str, str]]]:
    """把 Markdown 渲染为正文 HTML 与目录。

    Args:
        markdown: 源文档全文

    Returns:
        ``(正文 HTML, [(层级, 锚点, 标题文本), ...])``
    """
    lines = markdown.splitlines()
    out: list[str] = []
    toc: list[tuple[int, str, str]] = []
    used: dict[str, int] = {}
    index = 0
    pending_list: str | None = None

    def close_list() -> None:
        nonlocal pending_list
        if pending_list is not None:
            out.append(f"</{pending_list}>")
            pending_list = None

    while index < len(lines):
        line = lines[index]

        fence = _FENCE_RE.match(line)
        if fence:
            close_list()
            language = fence.group(1)
            index += 1
            block: list[str] = []
            while index < len(lines) and not lines[index].startswith("```"):
                block.append(lines[index])
                index += 1
            index += 1
            css = "diagram" if language == "mermaid" else ""
            klass = f' class="{css}"' if css else ""
            out.append(f"<pre{klass}><code>{html.escape(chr(10).join(block))}</code></pre>")
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            close_list()
            level = len(heading.group(1))
            text = heading.group(2).strip()
            anchor = _slug(text, used)
            if level <= 3:
                toc.append((level, anchor, _INLINE_CODE_RE.sub(r"\1", text)))
            out.append(f'<h{level} id="{anchor}">{_inline(text)}</h{level}>')
            index += 1
            continue

        if line.strip().startswith("|"):
            close_list()
            block = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                block.append(lines[index])
                index += 1
            if not block:
                continue
            head = _cells(block[0])
            body = block[2:] if len(block) > 1 and _SEPARATOR_RE.match(block[1]) else block[1:]
            out.append("<table>")
            header = "".join(f"<th>{_inline(c)}</th>" for c in head)
            out.append(f"<thead><tr>{header}</tr></thead>")
            out.append("<tbody>")
            for row in body:
                cells = _cells(row)
                padded = cells + [""] * (len(head) - len(cells))
                out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in padded) + "</tr>")
            out.append("</tbody></table>")
            continue

        if _SEPARATOR_RE.match(line) and "---" in line:
            close_list()
            out.append("<hr>")
            index += 1
            continue

        bullet = _BULLET_RE.match(line)
        ordered = _ORDERED_RE.match(line)
        if bullet or ordered:
            tag = "ul" if bullet else "ol"
            if pending_list != tag:
                close_list()
                out.append(f"<{tag}>")
                pending_list = tag
            content = bullet.group(1) if bullet else ordered.group(2)  # type: ignore[union-attr]
            out.append(f"<li>{_inline(content)}</li>")
            index += 1
            continue

        if line.strip().startswith(">"):
            close_list()
            quoted: list[str] = []
            while index < len(lines) and lines[index].strip().startswith(">"):
                quoted.append(lines[index].strip().lstrip(">").strip())
                index += 1
            out.append(f"<blockquote><p>{_inline(' '.join(quoted))}</p></blockquote>")
            continue

        if not line.strip():
            close_list()
            index += 1
            continue

        close_list()
        paragraph: list[str] = []
        while index < len(lines):
            candidate = lines[index]
            if (
                not candidate.strip()
                or _FENCE_RE.match(candidate)
                or _HEADING_RE.match(candidate)
                or candidate.strip().startswith(("|", ">"))
                or _BULLET_RE.match(candidate)
                or _ORDERED_RE.match(candidate)
                or (_SEPARATOR_RE.match(candidate) and "---" in candidate)
            ):
                break
            paragraph.append(candidate.strip())
            index += 1
        if paragraph:
            out.append(f"<p>{_inline(' '.join(paragraph))}</p>")
        else:
            index += 1

    close_list()
    return "\n".join(out), toc


def _sidebar(toc: list[tuple[int, str, str]]) -> str:
    """生成侧边目录。

    Args:
        toc: 目录项（层级、锚点、标题）

    Returns:
        导航 HTML
    """
    links = []
    for level, anchor, text in toc:
        klass = ' class="lv3"' if level == 3 else ""
        links.append(f'<a href="#{anchor}"{klass}>{_inline(text)}</a>')
    return "\n".join(links)


def main() -> None:
    """生成 ``docs/ARCHITECTURE.html``。"""
    markdown = SOURCE.read_text(encoding="utf-8")
    body, toc = render(markdown)
    title_match = re.search(r"^#\s+(.+)$", markdown, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else "架构说明"
    document = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{_CSS}</style>
</head>
<body>
<button class="toggle" type="button">目录</button>
<aside class="sidebar">
  <div class="brand">
    <div class="mk">G</div>
    <h1>{html.escape(title)}</h1>
    <p>由 docs/ARCHITECTURE.md 生成 · 文字以 Markdown 为准</p>
  </div>
  <div class="search"><input type="search" placeholder="过滤目录…" aria-label="过滤目录"></div>
  <nav class="nav">
{_sidebar(toc)}
  </nav>
</aside>
<main class="main">
{body}
</main>
<script>{_JS}</script>
</body>
</html>
"""
    TARGET.write_text(document, encoding="utf-8", newline="\n")
    print(f"wrote {TARGET.relative_to(ROOT)} ({len(document)} chars, {len(toc)} nav items)")


if __name__ == "__main__":
    main()
