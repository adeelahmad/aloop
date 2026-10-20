"""The paths an apply_patch patch adds, updates, deletes or moves to, read as loosely as a host parses a
header: Codex trims each line (Rust's trim), OpenCode cuts the path after the colon and trims it (JS trim,
which also cuts a U+FEFF). One copy for the door gate, host-adapter and both scope gates."""
import re

WS = r"(?:[^\S\n]|﻿)"
PATCH = re.compile(r"^{0}*\*\*\*{0}*(?:Add|Update|Delete){0}+File:{0}*(.+)$|^{0}*\*\*\*{0}*Move{0}+to:{0}*(.+)$".format(WS),
                   re.M)
EDGE = re.compile(r"^(?:\s|﻿)+|(?:\s|﻿)+$")
PATCHERS = ("apply_patch", "applypatch")    # Codex's patch tool run as a program (its argument or stdin form)


def paths(text):
    """Each path a patch names, trimmed of what either host's trim cuts (Python's whitespace and JS's)."""
    return [x for x in (EDGE.sub("", a or b) for a, b in PATCH.findall(text or "")) if x]
