"""A small S-expression reader for KiCad files (.kicad_pcb, .kicad_mod).

    parse(text) -> list   nested lists of str/float; strings keep no quotes

Atoms that look like numbers become floats; everything else stays a string.
It reads the whole file into memory: a 1 MB board parses in about a second.
"""
from __future__ import annotations

import re

_TOKEN = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))', re.S)
_NUM = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


def parse(text: str) -> list:
    stack: list[list] = [[]]
    pos, n = 0, len(text)
    while pos < n:
        m = _TOKEN.match(text, pos)
        if not m:
            if text[pos:].strip() == "":
                break
            raise ValueError(f"sexpr: can't read at offset {pos}: {text[pos:pos + 40]!r}")
        pos = m.end()
        if m.group(1):
            stack.append([])
        elif m.group(2):
            if len(stack) < 2:
                raise ValueError(f"sexpr: unbalanced ')' at offset {pos}")
            done = stack.pop()
            stack[-1].append(done)
        elif m.group(3) is not None:
            stack[-1].append(Str(m.group(3).replace('\\"', '"').replace("\\\\", "\\")))
        else:
            a = m.group(4)
            stack[-1].append(float(a) if _NUM.match(a) else a)
    if len(stack) != 1:
        raise ValueError("sexpr: unbalanced '('")
    top = stack[0]
    return top[0] if len(top) == 1 else top


class Str(str):
    """A quoted string, so a quoted "12" stays text and isn't a number."""


def head(node) -> str | None:
    return node[0] if isinstance(node, list) and node and isinstance(node[0], str) else None


def find(node: list, name: str) -> list | None:
    """The first child list named `name`."""
    for c in node[1:]:
        if head(c) == name:
            return c
    return None


def find_all(node: list, name: str) -> list[list]:
    return [c for c in node[1:] if head(c) == name]


def value(node: list, name: str, default=None):
    """The first atom of the child named `name`: (name v ...) -> v."""
    c = find(node, name)
    return c[1] if c is not None and len(c) > 1 else default
