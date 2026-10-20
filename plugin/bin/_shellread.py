"""_shellread.py — read a Bash command the way the shell would, without running it.

gate-mission-scope judges what a command writes; this module only reads. read(src) gives the
simple commands of `src` in order, each a Cmd with its assignments, words and redirects, and the
"(" / ")" marks of subshells. It follows the shell's own rules:
  - quotes ('…', "…", $'…'), backslash escapes and line continuations;
  - `#` comments, and heredoc bodies as data (an unquoted heredoc's $(…) still runs: kept; the body
    text is kept on the redirect's word, so a shell reading it on stdin can be read again);
  - reserved words (if, then, do, !, { …), `for … in` lists (Cmd.loop binds the variable), `case`
    patterns, function bodies; a simple `{a,b}` brace expands to its words;
  - a compound ({ … }, if/for/while/until/case) and each pipeline element scope `cd`, so a `cd`
    inside one does not move where a later write lands (the caller pushes/pops the scope);
  - [[ … ]], (( … )) and $(( … )): a `>` there compares, it never redirects; `]]` ends the test even
    when glued to `;`, `&&`, `||` or `)`;
  - $(…), `…`, <(…) and >(…): their insides are commands of their own (Cmd.subs).
A word is a list of parts: a literal str, or a tuple for what only running it would tell:
("var", name, default parts or None), ("cmd", text) for $(…) and `…`, ("proc", text) for <(…),
UNKNOWN for a glob, arithmetic or a positional parameter. The caller resolves what it can.
A heredoc's word carries its body (Word.body), and a Cmd the operator that ended it (Cmd.end:
"|" feeds the next command's stdin), for the door gate (plugin/mission/doors.py), which reads
what a shell or python is fed.
"""
import re

UNKNOWN = ("unknown",)
RESERVED = {"if", "then", "elif", "else", "fi", "do", "done", "while", "until", "!", "{", "}", "time", "coproc"}
STARTS = {"if", "then", "elif", "else", "do", "while", "until", "!", "{", "time", "for"}   # a command follows
REDIR = re.compile(r"(\d+|\{[A-Za-z_][A-Za-z0-9_]*\})?(&>>|&>|>>|>\||>&|<<<|<<-|<<|<>|<&|>|<)")
OPS = (";;&", ";;", ";&", "&&", "||", "|&", ";", "|", "&", "(", ")")
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
META = " \t\r\n;&|()<>"
META_RE = re.compile(r"[ \t\r\n;&|()<>]")
SPECIAL = "0123456789@*#?$!-"


class Word:
    def __init__(self):
        self.parts, self.raw, self.quoted, self.assign = [], "", False, None
        self.plain = True                                 # only unquoted literal text so far
        self.body = None                                  # a heredoc delimiter's body text (set by bodies())

    def lit(self, s):
        if s:
            if self.parts and isinstance(self.parts[-1], str):
                self.parts[-1] += s
            else:
                self.parts.append(s)

    def add(self, part):
        self.parts.append(part)
        self.plain = False

    def text(self):
        """The literal value, or None when part of it is known only at run time."""
        return "".join(self.parts) if all(isinstance(p, str) for p in self.parts) else None

    def value(self):
        """The parts after `NAME=` of an assignment."""
        parts, cut = list(self.parts), len(self.assign) + 1
        if parts and isinstance(parts[0], str):
            parts[0] = parts[0][cut:]
            if not parts[0]:
                parts.pop(0)
        return parts


class Cmd:
    def __init__(self):
        self.assigns, self.words, self.redirs, self.subs = [], [], [], []
        self.loop = None                                  # ("NAME", [list words]) for a `for`/`select` header
        self.pipe = False                                 # a pipeline element (its `cd` does not leak)
        self.end = None                                   # the operator that ended it (";", "|", "&&", …)
        self.own = False                                  # the redirects of the compound just closed (`} > f`)

    def empty(self):
        return not (self.assigns or self.words or self.redirs or self.subs or self.loop)


def expand(w):
    """Brace-expand a word: the words bash would make of a simple `{a,b}` brace (one word otherwise).
    Capped so it stays linear; a product over the cap falls back to one word with the brace unknown."""
    braces = [p for p in w.parts if isinstance(p, tuple) and p[0] == "brace"]
    if not braces:
        return [w]
    total = 1
    for p in braces:
        total *= len(p[1])
    if w.assign is not None or total > 64:                # an assignment RHS is not brace-expanded; a huge
        nw = Word()                                       # product would not stay linear: judge by the prefix
        nw.parts = [UNKNOWN if isinstance(p, tuple) and p[0] == "brace" else p for p in w.parts]
        nw.quoted, nw.plain, nw.assign, nw.raw = w.quoted, False, w.assign, w.raw
        return [nw]
    combos = [[]]
    for p in w.parts:
        if isinstance(p, tuple) and p[0] == "brace":
            combos = [c + [alt] for c in combos for alt in p[1]]
        else:
            combos = [c + [p] for c in combos]
    out = []
    for parts in combos:
        nw = Word()
        nw.parts = parts
        nw.quoted, nw.plain, nw.assign, nw.raw = w.quoted, False, w.assign, w.raw
        out.append(nw)
    return out


def subs_of(parts):
    """The command texts a word runs while it is expanded ($(…), `…`, <(…), and inside ${x:-…})."""
    out = []
    for p in parts:
        if isinstance(p, tuple) and p[0] in ("cmd", "proc"):
            out.append(p[1])
        elif isinstance(p, tuple) and p[0] == "var" and p[2]:
            out += subs_of(p[2])
    return out


def delimiter(raw):
    """A heredoc's delimiter as the shell takes it: the word as written, quotes removed and nothing
    expanded (`<<$X` ends at a line `$X`, `<<"E O"` at `E O`)."""
    out, i, n = [], 0, len(raw)
    while i < n:
        c = raw[i]
        if c == "\\":
            out.append(raw[i + 1:i + 2])
            i += 2
        elif c == "'" or (c == "$" and raw.startswith("$'", i)):
            i += 1 if c == "'" else 2
            k = raw.find("'", i)
            k = n if k < 0 else k
            out.append(raw[i:k])
            i = k + 1
        elif c == '"' or (c == "$" and raw.startswith('$"', i)):
            i += 1 if c == '"' else 2
            while i < n and raw[i] != '"':
                if raw[i] == "\\" and raw[i + 1:i + 2] in ('"', "\\", "$", "`"):
                    i += 1
                out.append(raw[i:i + 1])
                i += 1
            i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


class Lexer:
    def __init__(self, src):
        self.s, self.i, self.n = src, 0, len(src)
        self.toks, self.heredocs, self.dbr = [], [], False
        self._endc, self._closed = (0, 0), {}

    # ── helpers ──
    def at_start(self):
        """Does a command start here (so `((` is arithmetic and `[[` a test)?"""
        if not self.toks:
            return True
        k, v = self.toks[-1][0], self.toks[-1][1]
        if k == "op":
            return True                                   # also after a case pattern's `)`
        return k == "word" and not v.quoted and v.text() in STARTS

    def close(self, j, depth=1, arith=False):
        """close() remembered: a nested $((…)) is tried as arithmetic, then as a substitution, so
        without it a deep nest is read in exponential time."""
        key = (j, depth, arith)
        if key not in self._closed:
            self._closed[key] = self._close(j, depth, arith)
        return self._closed[key]

    def _close(self, j, depth=1, arith=False):
        """Index just past the `)` that closes an opening at depth `depth`, from j. Read as the shell
        does: quotes, backticks, `#` comments, ${…}, $[…], a nested $(…) or $((…)) (arithmetic only
        when it closes with `))`, else a substitution of a subshell, as bash falls back), `case`
        patterns (their `)` closes nothing) and, outside arithmetic (where << shifts), heredoc
        bodies. The end of the text if it never closes."""
        s, n, pending, cases, begin = self.s, self.n, [], [], j
        while j < n:
            c = s[j]
            if c == "\\":
                j += 2
                continue
            if c == "#" and not arith and (j == begin or s[j - 1] in " \t\n;&|()"):
                e = s.find("\n", j)
                j = n if e < 0 else e
                continue
            if c == "<" and not arith and s.startswith("<<<", j):
                j += 3
                continue
            if c == "<" and not arith and s.startswith("<<", j):
                strip = s.startswith("<<-", j)
                sub = Lexer(s)
                sub.i = j + (3 if strip else 2)
                while sub.i < n and s[sub.i] in " \t":
                    sub.i += 1
                w = sub.word()
                if w.raw:
                    pending.append((delimiter(w.raw), strip))
                j = max(sub.i, j + 2)
                continue
            if c == "\n" and pending:
                j, closed = self._skip_bodies(j + 1, pending)
                pending = []
                if closed:                                # a body ended at `DELIM)`: j is that `)`
                    depth -= 1
                    if depth == 0:
                        return j + 1
                    j += 1
                continue
            if c == "'":
                k = s.find("'", j + 1)
                j = n if k < 0 else k + 1
                continue
            if c == '"':
                j = self._dq_end(j + 1)
                continue
            if c == "`":
                j += 1
                while j < n and s[j] != "`":
                    j += 2 if s[j] == "\\" else 1
                j += 1
                continue
            if c == "$" and j + 1 < n:
                nx = s[j + 1]
                if nx == "(":
                    j = self._sub_end(j)
                    continue
                if nx == "{":
                    j = self._brace_end(j + 2)
                    continue
                if nx == "[":
                    j = self._bracket_end(j + 2)
                    continue
                if nx == "'":                             # $'…': backslash escapes a quote
                    j += 2
                    while j < n and s[j] != "'":
                        j += 2 if s[j] == "\\" else 1
                    j += 1
                    continue
                j += 2 if nx in SPECIAL else 1
                continue
            if c == "(" and s.startswith("((", j) and not arith:
                k = self.close(j + 2, 1, arith=True)
                if k < n and s[k] == ")":                 # (( … )): arithmetic
                    j = k + 1
                    continue
            m = NAME.match(s, j) if not arith and (j == begin or s[j - 1] in " \t\n;&|()") else None
            if m:
                word = m.group()
                if m.end() == n or s[m.end()] in META:
                    top = cases[-1] if cases else None
                    if word == "case" and top != "word" and self._cmd_pos(j, begin):
                        cases.append("word")
                    elif word == "in" and top == "word":
                        cases[-1] = "pattern"
                    elif word == "esac" and top in ("pattern", "body"):
                        cases.pop()
                j = m.end()
                continue
            if cases and cases[-1] == "pattern" and c in "()":
                if c == ")":
                    cases[-1] = "body"
                j += 1
                continue
            if cases and cases[-1] == "body" and c == ";" and s.startswith(";;", j):
                cases[-1] = "pattern"
                j += 3 if s.startswith(";;&", j) else 2
                continue
            if cases and cases[-1] == "body" and s.startswith(";&", j):
                cases[-1] = "pattern"
                j += 2
                continue
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    return j + 1
            j += 1
        return n

    def _cmd_pos(self, j, begin):
        """Is the word at j where a command starts (so `case` is the reserved word)?"""
        k = j - 1
        while k >= begin and self.s[k] in " \t":
            k -= 1
        if k < begin or self.s[k] in ";&|(\n":
            return True
        m = re.search(r"(?:^|[\s;&|()])([A-Za-z!{]+)$", self.s[begin:k + 1])
        return bool(m) and m.group(1) in STARTS

    def _sub_end(self, j):
        """Past a $(…) or $((…)) that starts at j (its `$`)."""
        s, n = self.s, self.n
        if s.startswith("$((", j):
            k = self.close(j + 3, 1, arith=True)
            if k < n and s[k] == ")":
                return k + 1
        return self.close(j + 2)

    def _dq_end(self, j):
        """Past the `"` that closes a double-quoted string whose text starts at j; its $(…), ${…} and
        `…` are read whole (a `"` inside them does not close it)."""
        s, n = self.s, self.n
        while j < n and s[j] != '"':
            c = s[j]
            if c == "\\":
                j += 2
            elif c == "$" and s.startswith("$(", j):
                j = self._sub_end(j)
            elif c == "$" and s.startswith("${", j):
                j = self._brace_end(j + 2)
            elif c == "`":
                j += 1
                while j < n and s[j] != "`":
                    j += 2 if s[j] == "\\" else 1
                j += 1
            else:
                j += 1
        return j + 1

    def _brace_end(self, j):
        """Past the `}` that closes a ${ whose text starts at j (nested ${…}, $(…) and quotes read whole)."""
        s, n, depth = self.s, self.n, 1
        while j < n:
            c = s[j]
            if c == "\\":
                j += 2
                continue
            if c == "'":
                k = s.find("'", j + 1)
                j = n if k < 0 else k + 1
                continue
            if c == '"':
                j = self._dq_end(j + 1)
                continue
            if c == "$" and s.startswith("$(", j):
                j = self._sub_end(j)
                continue
            if c == "$" and s.startswith("${", j):
                depth += 1
                j += 2
                continue
            if c == "}":
                depth -= 1
                if depth == 0:
                    return j + 1
            j += 1
        return n

    def _bracket_end(self, j):
        """Past the `]` that closes a $[ (arithmetic) whose text starts at j."""
        s, n, depth = self.s, self.n, 1
        while j < n:
            if s[j] == "[":
                depth += 1
            elif s[j] == "]":
                depth -= 1
                if depth == 0:
                    return j + 1
            j += 1
        return n

    def _skip_bodies(self, j, pending):
        """(past the heredoc bodies that start at j, whether one ended at `DELIM)`). Inside $( … ) a
        body also ends at `DELIM)`: the `)` closes the substitution there (j is it), and the bodies
        still pending would start after it, outside: the rest of that line is commands again."""
        s = self.s
        for delim, strip in pending:
            while j < self.n:
                e = s.find("\n", j)
                e = self.n if e < 0 else e
                line = s[j:e]
                t = line.lstrip("\t") if strip else line
                if t == delim:
                    j = min(e + 1, self.n)
                    break
                if t.startswith(delim) and t[len(delim):].lstrip(" \t").startswith(")"):
                    return e - len(t[len(delim):].lstrip(" \t")), True
                j = min(e + 1, self.n)
        return j, False

    def backtick(self):
        s, j, out = self.s, self.i + 1, []
        while j < self.n and s[j] != "`":
            if s[j] == "\\" and j + 1 < self.n and s[j + 1] in "`\\$":
                out.append(s[j + 1])
                j += 2
                continue
            out.append(s[j])
            j += 1
        self.i = j + 1
        return "".join(out)

    def dollar(self, w):
        s, i = self.s, self.i
        nxt = s[i + 1] if i + 1 < self.n else ""
        k = self.close(i + 3, 1, arith=True) if s.startswith("$((", i) else 0
        if k and k < self.n and s[k] == ")":
            self.i = k + 1                                # $(( … )): arithmetic (else a $( ( … ) … ))
            w.add(UNKNOWN)
        elif nxt == "[":
            self.i = self._bracket_end(i + 2)             # $[ … ]: arithmetic
            w.add(UNKNOWN)
        elif nxt == "(":
            j = self.close(i + 2)
            w.add(("cmd", s[i + 2:j - 1]))
            self.i = j
        elif nxt == "{":
            j, depth = i + 2, 1
            while j < self.n and depth:
                if s[j] == "\\":
                    j += 1
                elif s[j] == "{":
                    depth += 1
                elif s[j] == "}":
                    depth -= 1
                j += 1
            inner = s[i + 2:j - 1]
            self.i = j
            m = NAME.match(inner)
            if not m:
                w.add(UNKNOWN)                            # ${1}, ${#x}, ${!x}
                return
            rest = inner[m.end():]
            op = re.match(r":?[-=]", rest)
            if not rest:
                w.add(("var", m.group(), None))
            elif op:
                sub = Lexer(rest[op.end():])
                dw = sub.word(stop="")
                w.add(("var", m.group(), dw.parts))
            else:
                w.add(UNKNOWN)                            # ${x%…}, ${x/…}, ${x:1}, ${x[0]}
        elif nxt == "'":
            j, out = i + 2, []
            esc = {"n": "\n", "t": "\t", "\\": "\\", "'": "'", '"': '"', "e": "\x1b", "a": "\a", "r": "\r"}
            while j < self.n and s[j] != "'":
                if s[j] == "\\" and j + 1 < self.n:
                    out.append(esc.get(s[j + 1], s[j + 1]))
                    j += 2
                    continue
                out.append(s[j])
                j += 1
            w.lit("".join(out))
            w.quoted, w.plain = True, False
            self.i = j + 1
        elif nxt == '"':
            self.i += 1
            self.dquote(w)
        elif nxt and NAME.match(nxt):
            m = NAME.match(s, i + 1)
            w.add(("var", m.group(), None))
            self.i = m.end()
        elif nxt and nxt in SPECIAL:
            w.add(UNKNOWN)
            self.i += 2
        else:
            w.lit("$")
            self.i += 1

    def dquote(self, w):
        s = self.s
        self.i += 1
        w.quoted, w.plain = True, False
        while self.i < self.n and s[self.i] != '"':
            c = s[self.i]
            if c == "\\" and self.i + 1 < self.n:
                if s[self.i + 1] == "\n":
                    self.i += 2
                elif s[self.i + 1] in '$`"\\':
                    w.lit(s[self.i + 1])
                    self.i += 2
                else:
                    w.lit("\\")
                    self.i += 1
            elif c == "$":
                nx = s[self.i + 1] if self.i + 1 < self.n else ""
                if nx in "({" or nx in SPECIAL or NAME.match(nx or ""):
                    self.dollar(w)                        # an expansion ($(, ${, $name, $1, $@ …)
                else:
                    w.lit("$")                            # `$"` / `$'` / `$ ` inside "…" is a literal $
                    self.i += 1
            elif c == "`":
                w.add(("cmd", self.backtick()))
            else:
                w.lit(c)
                self.i += 1
        self.i += 1

    def word(self, stop=META):
        """One word from self.i; stops at an unquoted character of `stop`."""
        s, w, start = self.s, Word(), self.i
        while self.i < self.n:
            c = s[self.i]
            if c == "\\":
                if self.i + 1 < self.n and s[self.i + 1] == "\n":
                    self.i += 2
                    continue
                w.lit(s[self.i + 1:self.i + 2])
                w.quoted, w.plain = True, False
                self.i += 2
                continue
            if c in "<>" and s.startswith("(", self.i + 1) and c in stop:
                j = self.close(self.i + 2)
                w.add(("proc", s[self.i + 2:j - 1]))
                self.i = j
                continue
            if c == "(" and w.assign is not None and w.plain and w.text() == w.assign + "=":
                j = self.close(self.i + 1)            # an array: a=( … ); its substitutions still run
                for t in Lexer(s[self.i + 1:j - 1]).run():
                    for sub in subs_of(t[1].parts) if t[0] == "word" else []:
                        w.add(("cmd", sub))
                self.i = j
                w.add(UNKNOWN)
                continue
            if c in stop:
                break
            if c == "'":
                j = s.find("'", self.i + 1)
                j = self.n if j < 0 else j
                w.lit(s[self.i + 1:j])
                w.quoted, w.plain = True, False
                self.i = j + 1
            elif c == '"':
                self.dquote(w)
            elif c == "$":
                self.dollar(w)
            elif c == "`":
                w.add(("cmd", self.backtick()))
            elif c in "*?":
                w.add(UNKNOWN)
                self.i += 1
            elif c == "[" and self.i > start and s.find("]", self.i + 1, self._end()) >= 0:
                self.i = s.index("]", self.i + 1) + 1
                w.add(UNKNOWN)
            elif c == "{" and self._brace(w):
                pass
            elif c == "~" and self.i == start:
                m = re.match(r"~([A-Za-z0-9_.-]*)", s[self.i:])
                w.add(("var", "HOME", None) if not m.group(1) else UNKNOWN)
                self.i += m.end()
            elif c == "=" and w.assign is None and w.plain and NAME.fullmatch(w.text() or ""):
                w.assign = w.text()
                w.lit("=")
                self.i += 1
            else:
                w.lit(c)
                self.i += 1
        w.raw = s[start:self.i]
        return w

    def _end(self):
        """Where the word at self.i ends at the latest (the next metacharacter); kept for the word,
        so a word of many `[` or `{` is read in linear time."""
        if self._endc[0] <= self.i < self._endc[1]:
            return self._endc[1]
        m = META_RE.search(self.s, self.i)
        self._endc = (self.i, m.start() if m else self.n)
        return self._endc[1]

    def _brace(self, w):
        """A brace expansion is many words. A simple `{a,b}` of plain literals keeps its alternatives
        (so each resulting word is judged); a range `{1..3}` or anything non-literal stays unknown."""
        k = self.s.find("}", self.i + 1, self._end())
        if k < 0:
            return False
        body = self.s[self.i + 1:k]
        if "," not in body and ".." not in body:
            return False
        if ".." not in body and not re.search(r"[{}$`'\"]", body):
            w.add(("brace", body.split(",")))             # {a,b,c} → expanded by expand()
        else:
            w.add(UNKNOWN)
        self.i = k + 1
        return True

    def bodies(self):
        """The heredoc bodies that start after a newline: data, except the substitutions of an
        unquoted one, which run."""
        s = self.s
        for delim, strip, quoted, dw in self.heredocs:
            lines = []
            while self.i < self.n:
                j = s.find("\n", self.i)
                j = self.n if j < 0 else j
                line = s[self.i:j]
                self.i = min(j + 1, self.n)
                if (line.lstrip("\t") if strip else line) == delim:
                    break
                lines.append(line.lstrip("\t") if strip else line)
                if not quoted and ("$(" in line or "`" in line):
                    sub = Lexer(line)
                    w = Word()
                    while sub.i < sub.n:                  # the line read as inside double quotes
                        c = line[sub.i]
                        if c == "\\":
                            sub.i += 2
                        elif c == "$":
                            sub.dollar(w)
                        elif c == "`":
                            w.add(("cmd", sub.backtick()))
                        else:
                            sub.i += 1
                    for t in subs_of(w.parts):
                        self.toks.append(("sub", t))
            dw.body = "\n".join(lines)                     # kept so a shell reading it on stdin can be read again
        self.heredocs = []

    # ── the token stream ──
    def run(self):
        s = self.s
        while self.i < self.n:
            c = s[self.i]
            if c == "\\" and s.startswith("\\\n", self.i):
                self.i += 2
                continue
            if c in " \t\r":
                self.i += 1
                continue
            if c == "\n":
                self.i += 1
                self.toks.append(("op", "\n"))
                self.bodies()
                continue
            if c == "#":
                j = s.find("\n", self.i)
                self.i = self.n if j < 0 else j
                continue
            if self.dbr:
                w = self.word(stop=" \t\r\n")
                raw = w.raw
                if raw.startswith("]]") and (len(raw) == 2 or raw[2] in META):
                    self.i -= len(raw) - 2                 # `]]` glued to ; && || ): end the test, hand back the op
                    cw = Word()
                    cw.lit("]]")
                    cw.raw = "]]"
                    self.toks.append(("word", cw))
                    self.dbr = False
                else:
                    self.toks.append(("word", w))
                    if not raw:                            # nothing consumed: never loop forever
                        self.i += 1
                continue
            if s.startswith("((", self.i) and self.at_start():
                k = self.close(self.i + 2, 1, arith=True)
                if k < self.n and s[k] == ")":
                    self.toks.append(("arith", s[self.i:k + 1]))
                    self.i = k + 1
                    continue
            m = REDIR.match(s, self.i)
            if m and not (m.group(2) in ("<", ">") and s.startswith("(", m.end())):
                self.i = m.end()
                while self.i < self.n and s[self.i] in " \t":
                    self.i += 1
                w = self.word()
                op = m.group(2)
                if op in ("<<", "<<-") and w.raw:
                    self.heredocs.append((delimiter(w.raw), op == "<<-", w.quoted, w))
                self.toks.append(("redir", op, m.group(1) or "", w))
                continue
            op = next((o for o in OPS if s.startswith(o, self.i)), None)
            if op:
                self.toks.append(("op", op))
                self.i += len(op)
                continue
            start_here = self.at_start()
            w = self.word()
            if not w.raw:                                 # nothing consumed: never loop forever
                self.i += 1
                continue
            for ew in expand(w):                          # one word, unless a `{a,b}` brace expands it
                self.toks.append(("word", ew))
            if start_here and not w.quoted and w.text() == "[[":
                self.dbr = True
        return self.toks


def read(src):
    """[Cmd | "(" | ")" | "cwd+" | "cwd|" | "cwd-"] in the order the shell runs them. "cwd+"/"cwd-" bound an
    if, case, loop body or `{ …; }` group (a function's too), whose `cd` may or may not run; "cwd|" ends one of
    its branches (elif, else, a case's ;;); "(" / ")" bound a subshell, whose `cd` the shell forgets."""
    out, cur = [], Cmd()
    mode, cases, skip_name, func, after_time = None, 0, False, False, False
    loop_name, loop_list, loop_have, loop_in, pipe_next = None, [], False, False, False
    closing = False                                       # a compound just closed: a redirect now is its own

    def flush():
        nonlocal cur
        if not cur.empty():
            out.append(cur)
        cur = Cmd()

    try:
        toks = Lexer(src).run()
    except RecursionError:
        raise ValueError("the command nests substitutions deeper than the shell reader reads") from None
    for k, tok in enumerate(toks):
        kind = tok[0]
        if kind == "redir" and closing and cur.empty():
            cur.own = True
        closing = kind == "redir" and closing
        if kind == "word":
            w = tok[1]
            t = None if w.quoted else w.text()
            cur.subs += subs_of(w.parts)
            if mode == "header":                          # for NAME in …: the variable, then its list
                if not loop_have:
                    loop_name, loop_have = t, True        # None when not a literal name: no binding
                elif t == "in":
                    loop_in = True
                elif loop_in:
                    loop_list.append(w)
                elif t == "do":                           # for NAME do …: "$@", and the body starts
                    mode = None
                    if loop_name is not None:
                        lc = Cmd()
                        lc.loop = (loop_name, [])
                        out.append(lc)
                    out.append("cwd+")
                continue
            if mode == "case":                            # case WORD in
                if t == "in":
                    mode = "pattern"
                continue
            if mode == "pattern":
                if t == "esac":
                    flush()
                    out.append("cwd-")
                    closing = True
                    cases -= 1
                    mode = None
                continue
            start = not cur.words and not cur.assigns
            if start and pipe_next:
                cur.pipe, pipe_next = True, False
            if start and after_time and t in ("-p", "--") and not w.quoted:
                after_time = t == "-p"                    # `time -p -- CMD`: the `--` ends time's options
                continue
            after_time = False
            if start and t in ("if", "do", "{"):          # a compound/group opens: its `cd` is scoped
                flush()
                out.append("cwd+")
                continue
            if start and t in ("fi", "done", "}"):
                flush()
                out.append("cwd-")
                closing = True
                continue
            if start and t in ("elif", "else"):           # the branch before it ends; the next starts afresh
                flush()
                out.append("cwd|")
                continue
            if start and t in RESERVED:
                after_time = t == "time"
                nxt = toks[k + 2] if k + 2 < len(toks) else ("",)
                if t == "coproc" and (nxt == ("op", "(") or nxt[0] == "word" and nxt[1].text() == "{"):
                    skip_name = True                      # coproc NAME { …; }: NAME names it, it does not run
                continue
            if start and t in ("for", "select"):
                flush()
                mode, loop_name, loop_list, loop_have, loop_in = "header", None, [], False, False
                continue
            if start and t == "case":
                flush()
                out.append("cwd+")
                cases += 1
                mode = "case"
                continue
            if start and t == "esac" and cases:
                flush()
                out.append("cwd-")
                closing = True
                cases -= 1
                continue
            if start and t == "function":
                skip_name = True
                continue
            if skip_name:
                skip_name = False
                continue
            if start and w.assign is not None:
                cur.assigns.append((w.assign, w.value()))
                continue
            cur.words.append(w)
        elif kind == "op":
            v = tok[1]
            if mode == "pattern":
                if v == ")":
                    mode = None
                continue
            if mode == "header":
                if v in (";", "\n"):
                    mode = None
                    if loop_have and loop_name is not None:
                        lc = Cmd()
                        lc.loop = (loop_name, loop_list)
                        for lw in loop_list:
                            lc.subs += subs_of(lw.parts)
                        out.append(lc)
                continue
            if mode == "case":
                continue
            if v == "(":
                if cur.words and not cur.redirs:          # name ( ): a function definition
                    cur, func = Cmd(), True
                    continue
                cur.end = v
                flush()
                out.append("(")
                continue
            if v == ")":
                if func:
                    func = False
                    continue
                cur.end = v
                flush()
                out.append(")")
                continue
            cur.end = v
            if v in ("|", "|&"):                           # a pipeline element runs in a subshell
                cur.pipe = True
                flush()
                pipe_next = True
                continue
            flush()
            pipe_next = False
            if v in (";;", ";&", ";;&") and cases:
                mode = "pattern"
                out.append("cwd|")                        # a case branch ends (a `;&` fall-through is read as one too)
        elif kind == "redir":
            cur.redirs.append((tok[1], tok[2], tok[3]))
            cur.subs += subs_of(tok[3].parts)
        elif kind == "arith":
            if mode != "header":
                flush()
        elif kind == "sub":
            flush()
            c = Cmd()
            c.subs.append(tok[1])
            out.append(c)
    flush()
    return out
