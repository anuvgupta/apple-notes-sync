"""Convert Apple Notes HTML bodies to Markdown.

Apple Notes writes one <div> per line, <div><br></div> for a blank line,
<h1>-<h3> for headings, <ul class="Apple-dash-list">/<ol> for lists (nested
lists sit directly inside their parent list), <b>/<i>/<u>/<strike>, <tt> for
monospace, <table> for tables, and embeds images as data: URIs.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import List, Union

BLOCK_TAGS = {
    "div", "p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li",
    "table", "thead", "tbody", "tfoot", "tr", "td", "th", "pre", "blockquote",
}
VOID_TAGS = {"br", "img", "hr", "input", "meta", "link", "col", "wbr", "source"}
STYLE_TAGS = {"b", "strong", "i", "em", "s", "strike", "del", "tt", "code", "u", "font", "span"}
LIST_INDENT = "    "

DATA_URI = re.compile(r"""(src|href)\s*=\s*(["'])data:.*?\2""", re.IGNORECASE | re.DOTALL)


class Node:
    __slots__ = ("tag", "attrs", "children")

    def __init__(self, tag: str, attrs: dict) -> None:
        self.tag = tag
        self.attrs = attrs
        self.children: List[Union["Node", str]] = []


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("root", {})
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: v or "" for k, v in attrs})
        self.stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(Node(tag, {k: v or "" for k, v in attrs}))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def html_to_markdown(html: str) -> str:
    builder = _TreeBuilder()
    builder.feed(DATA_URI.sub(r'\1=""', html))
    builder.close()
    _merge_runs(builder.root)
    lines = [line.rstrip() for line in _blocks(builder.root.children)]
    out: List[str] = []
    for line in lines:
        if not line and (not out or not out[-1]):
            continue
        out.append(line)
    while out and not out[-1]:
        out.pop()
    return "\n".join(out) + "\n" if out else ""


def _merge_runs(node: Node) -> None:
    """Join adjacent same-style siblings: Notes splits styled text into runs
    like <b>a</b><b>b</b>, which would otherwise render as **a****b**."""
    merged: List[Union[Node, str]] = []
    for child in node.children:
        prev = merged[-1] if merged else None
        if (
            isinstance(child, Node) and isinstance(prev, Node)
            and child.tag in STYLE_TAGS and child.tag == prev.tag and child.attrs == prev.attrs
        ):
            prev.children.extend(child.children)
        else:
            merged.append(child)
    node.children = merged
    for child in merged:
        if isinstance(child, Node):
            _merge_runs(child)


def _blocks(children) -> List[str]:
    """Render a run of siblings as lines; inline runs become lines, blocks recurse."""
    lines: List[str] = []
    buf: List[str] = []

    def flush() -> None:
        if not buf:
            return
        text = "".join(buf)
        buf.clear()
        if text.endswith("\n"):
            text = text[:-1]
        lines.extend(text.split("\n"))

    for child in children:
        if isinstance(child, str):
            if "\n" in child and not child.strip():
                continue
            buf.append(_text(child))
        elif child.tag in BLOCK_TAGS:
            if buf and not "".join(buf).strip():
                buf.clear()
            flush()
            lines.extend(_block(child))
        else:
            buf.append(_inline(child))
    flush()
    return lines


def _block(node: Node) -> List[str]:
    tag = node.tag
    if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
        text = " ".join(line.strip() for line in _blocks(node.children) if line.strip())
        text = _unwrap(text, "**")
        return ["#" * int(tag[1]) + " " + text] if text else [""]
    if tag in ("ul", "ol"):
        return _list(node)
    if tag == "li":
        orphan = Node("ul", {})
        orphan.children.append(node)
        return _list(orphan)
    if tag == "table":
        return _table(node)
    if tag == "pre":
        return ["```"] + _plain(node).split("\n") + ["```"]
    if tag == "blockquote":
        return ["> " + line if line else ">" for line in _blocks(node.children)]
    return _blocks(node.children)


def _list(node: Node) -> List[str]:
    ordered = node.tag == "ol"
    out: List[str] = []
    n = 0
    for item in node.children:
        if isinstance(item, str):
            if item.strip():
                n += 1
                out.append(("%d. " % n if ordered else "- ") + _text(item).strip())
            continue
        if item.tag in ("ul", "ol"):
            out.extend(LIST_INDENT + line if line else line for line in _list(item))
            continue
        n += 1
        marker = "%d. " % n if ordered else "- "
        inner = [line for line in _blocks(item.children if item.tag == "li" else [item]) if line.strip()]
        if not inner:
            out.append(marker.rstrip())
            continue
        out.append(marker + inner[0].lstrip())
        out.extend(LIST_INDENT + line for line in inner[1:])
    return out


def _table(node: Node) -> List[str]:
    rows: List[List[str]] = []

    def walk(n: Node) -> None:
        for child in n.children:
            if isinstance(child, str):
                continue
            if child.tag == "tr":
                cells = [c for c in child.children if not isinstance(c, str) and c.tag in ("td", "th")]
                rows.append([
                    " ".join(line.strip() for line in _blocks(c.children) if line.strip()).replace("|", "\\|")
                    for c in cells
                ])
            else:
                walk(child)

    walk(node)
    if not rows:
        return []
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["", "| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return lines + [""]


def _inline(node: Union[Node, str]) -> str:
    if isinstance(node, str):
        return _text(node)
    tag = node.tag
    if tag == "br":
        return "\n"
    if tag == "img":
        return "[image]"
    if tag in ("object", "attachment"):
        return "[attachment]"
    inner = "".join(_inline(c) for c in node.children)
    if tag in ("b", "strong"):
        return _wrap(inner, "**")
    if tag in ("i", "em"):
        return _wrap(inner, "*")
    if tag in ("s", "strike", "del"):
        return _wrap(inner, "~~")
    if tag in ("tt", "code"):
        return _wrap(inner, "`")
    if tag == "a":
        href = node.attrs.get("href", "")
        if not href or not inner.strip():
            return inner or href
        if inner.strip() == href:
            return "<%s>" % href
        return "[%s](%s)" % (inner.strip(), href)
    if tag in BLOCK_TAGS:
        return inner + "\n"
    return inner


def _wrap(text: str, marker: str) -> str:
    segments = []
    for seg in text.split("\n"):
        core = seg.strip()
        if not core:
            segments.append(seg)
            continue
        lead = seg[: len(seg) - len(seg.lstrip())]
        trail = seg[len(seg.rstrip()):]
        segments.append(lead + marker + core + marker + trail)
    return "\n".join(segments)


def _unwrap(text: str, marker: str) -> str:
    if len(text) > 2 * len(marker) and text.startswith(marker) and text.endswith(marker):
        inner = text[len(marker):-len(marker)]
        if marker not in inner:
            return inner
    return text


def _plain(node: Union[Node, str]) -> str:
    if isinstance(node, str):
        return node
    if node.tag == "br":
        return "\n"
    return "".join(_plain(c) for c in node.children)


def _text(data: str) -> str:
    return data.replace("\xa0", " ").replace("\r", "").replace("\n", " ")
