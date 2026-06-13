# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""
Schema-independent s-expression utilities for tests.

Deliberately NOT built on the zig sexp engine: these helpers audit and corrupt
files *around* the engine under test. Atoms are kept as raw token strings
(quotes included), so dumps() re-emits input tokens verbatim.

Tree shape: a node is a list whose items are either raw token strings (atoms)
or nested nodes. The first item is conventionally the node's head symbol.
"""

import re
from collections import Counter
from typing import Union

Node = list[Union[str, "Node"]]

_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|\(|\)|[^\s()"]+')


def parse(text: str) -> list[Node]:
    """Parse text into a list of top-level nodes."""
    tokens = _TOKEN.findall(text)
    stack: list[Node] = [[]]
    for tok in tokens:
        if tok == "(":
            node: Node = []
            stack[-1].append(node)
            stack.append(node)
        elif tok == ")":
            if len(stack) == 1:
                raise ValueError("unbalanced ')'")
            stack.pop()
        else:
            stack[-1].append(tok)
    if len(stack) != 1:
        raise ValueError("unbalanced '('")
    return stack[0]  # type: ignore[return-value]


def dumps(trees: list[Node] | Node) -> str:
    """Re-emit nodes as minimally formatted sexp text."""
    if trees and isinstance(trees[0], str):
        trees = [trees]  # single node

    def _render(item: Union[str, Node]) -> str:
        if isinstance(item, str):
            return item
        return "(" + " ".join(_render(i) for i in item) + ")"

    return "\n".join(_render(t) for t in trees) + "\n"


def head(node: Union[str, Node]) -> str | None:
    if isinstance(node, list) and node and isinstance(node[0], str):
        return node[0]
    return None


def children(node: Node, head_name: str) -> list[Node]:
    """Direct child nodes with the given head symbol."""
    return [c for c in node if isinstance(c, list) and head(c) == head_name]


def walk(node: Node):
    """Yield every list node in the tree, depth-first, including the root."""
    yield node
    for item in node:
        if isinstance(item, list):
            yield from walk(item)


def norm_atom(tok: str) -> str:
    """Normalize an atom for value comparison: 12.000000 == 12, and a quoted
    string equals the bare symbol with the same text (KiCad's reader tokenizes
    both forms to the same value — e.g. kicad-cli writes
    (property ki_fp_filters ...) where we write (property "ki_fp_filters" ...);
    quoting is lexical, not semantic)."""
    if tok.startswith('"') and tok.endswith('"') and len(tok) >= 2:
        tok = tok[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    try:
        return repr(float(tok))
    except ValueError:
        return tok


def leaf_multiset(trees: list[Node] | Node) -> Counter:
    """Flatten a tree into a multiset of (path-of-heads, item) entries.

    Each atom contributes ((..., head), value); each node contributes a
    structure marker ((...,), "(head") so dropped empty structures are caught.
    Child *order* is deliberately not part of the entry — reordering is
    tolerated, dropped/changed data is not.
    """
    if trees and isinstance(trees[0], str):
        trees = [trees]
    acc: Counter = Counter()

    def _visit(node: Node, path: tuple[str, ...]) -> None:
        h = head(node) or "?"
        acc[(path, "(" + h)] += 1
        sub = path + (h,)
        for item in node[1:] if head(node) else node:
            if isinstance(item, list):
                _visit(item, sub)
            else:
                acc[(sub, norm_atom(item))] += 1

    for t in trees:
        _visit(t, ())  # type: ignore[arg-type]
    return acc


def data_loss(raw: str, dump: str) -> Counter:
    """Multiset of entries present in `raw` but missing from `dump`.

    Empty result == nothing was dropped (modulo number formatting and child
    reordering). The keys aggregate naturally by sexp path, so the result
    doubles as a per-field inventory of schema gaps.
    """
    return leaf_multiset(parse(raw)) - leaf_multiset(parse(dump))


def summarize_loss(loss: Counter, limit: int = 20) -> str:
    by_path: Counter = Counter()
    for (path, _value), n in loss.items():
        by_path[path] += n
    lines = [
        f"  {'/'.join(p) or '<root>'}: {n} dropped"
        for p, n in by_path.most_common(limit)
    ]
    if len(by_path) > limit:
        lines.append(f"  ... and {len(by_path) - limit} more paths")
    return "\n".join(lines)
