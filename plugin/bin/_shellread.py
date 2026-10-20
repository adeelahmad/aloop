"""_shellread.py — read a Bash command the way the shell would, without running it.

gate-mission-scope judges what a command writes; this module only reads. read(src) gives the
simple commands of `src` in order, each a Cmd with its assignments, words and redirects, and the
"(" / ")" marks of subshells. It follows the shell's own rules:
  - quotes ('…', "…", $'…'), backslash escapes and line continuations;
  - `#` comments, and heredoc bodies as data (an unquoted heredoc's $(…) still runs: kept);
  - reserved words (if, then, do, !, { …), `for … in` lists, `case` patterns, function bodies;
  - [[ … ]], (( … )) and $(( … )): a `>` there compares, it never redirects;
  - $(…), `…`, <(…) and >(…): their insides are commands of their own (Cmd.subs).
A word is a list of parts: a literal str, or a tuple for what only running it would tell:
("var", name, default parts or None), ("cmd", text) for $(…) and `…`, ("proc", text) for <(…),
UNKNOWN for a glob, arithmetic or a positional parameter. The caller resolves what it can.
"""
import re

UNKNOWN = ("unknown",)
RESERVED = {"if", "then", "elif", "else", "fi", "do", "done", "while", "until", "!", "{", "}", "time", "coproc"}
STARTS = {"if", "then", "elif", "else", "do", "while", "until", "!", "{", "time", "for"}   # a command follows
REDIR = re.compile(r"(\d+|\{[A-Za-z_][A-Za-z0-9_]*\})?(&>>|&>|>>|>\||>&|<<<|<<-|<<|<>|<&|>|<)")
OPS = (";;&", ";;", ";&", "&&", "||", "|&", ";", "|", "&", "(", ")")
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
META = " \t\r\n;&|()<>"
SPECIAL = "0123456789@*#?$!-"


class Word:
    def __init__(self):
        self.parts, self.raw, self.quoted, self.assign = [], "", False, None
        self.plain = True                                 # only unquoted literal text so far

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

    def empty(self):
        return not (self.assigns or self.words or self.redirs or self.subs)


def subs_of(parts):
    """The command texts a word runs while it is expanded ($(…), `…`, <(…), and inside ${x:-…})."""
    out = []
    for p in parts:
        if isinstance(p, tuple) and p[0] in ("cmd", "proc"):
            out.append(p[1])
        elif isinstance(p, tuple) and p[0] == "var" and p[2]:
            out += subs_of(p[2])
    return out


class Lexer:
    def __init__(self, src):
        self.s, self.i, self.n = src, 0, len(src)
        self.toks, self.heredocs, self.dbr = [], [], False

    # ── helpers ──
    def at_start(self):
        """Does a command start here (so `((` is arithmetic and `[[` a test)?"""
        if not self.toks:
            return True
        k, v = self.toks[-1][0], self.toks[-1][1]
        if k == "op":
            return v != ")"
        return k == "word" and not v.quoted and v.text() in STARTS

    def close(self, j, depth=1):
        """Index just past the `)` that closes an opening at depth `depth`, from j; quotes,
        backticks and nested parens respected. The end of the text if it never closes."""
        s = self.s
        while j < self.n:
            c = s[j]
            if c == "\\":
                j += 2
                continue
            if c == "'":
                k = s.find("'", j + 1)
                j = self.n if k < 0 else k + 1
                continue
            if c == '"':
                j += 1
                while j < self.n and s[j] != '"':
                    j += 2 if s[j] == "\\" else 1
                j += 1
                continue
            if c == "`":
                j += 1
                while j < self.n and s[j] != "`":
                    j += 2 if s[j] == "\\" else 1
                j += 1
                continue
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    return j + 1
            j += 1
        return self.n

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
        if s.startswith("$((", i):
            self.i = self.close(i + 3, 2)
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
                self.dollar(w)
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
                self.i = self.close(self.i + 1)       # an array: a=( … )
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
            elif c == "[" and self.i > start and "]" in s[self.i + 1:self._end()]:
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
        j = self.i
        while j < self.n and self.s[j] not in META:
            j += 1
        return j

    def _brace(self, w):
        """A brace expansion ({a,b}, {1..3}) is many words: unknown."""
        k = self.s.find("}", self.i + 1, self._end())
        if k < 0:
            return False
        body = self.s[self.i + 1:k]
        if "," not in body and ".." not in body:
            return False
        w.add(UNKNOWN)
        self.i = k + 1
        return True

    def bodies(self):
        """The heredoc bodies that start after a newline: data, except the substitutions of an
        unquoted one, which run."""
        s = self.s
        for delim, strip, quoted in self.heredocs:
            while self.i < self.n:
                j = s.find("\n", self.i)
                j = self.n if j < 0 else j
                line = s[self.i:j]
                self.i = min(j + 1, self.n)
                if (line.lstrip("\t") if strip else line) == delim:
                    break
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
                self.toks.append(("word", w))
                if w.text() == "]]":
                    self.dbr = False
                continue
            if s.startswith("((", self.i) and self.at_start():
                j = self.close(self.i + 2, 2)
                self.toks.append(("arith", s[self.i:j]))
                self.i = j
                continue
            m = REDIR.match(s, self.i)
            if m and not (m.group(2) in ("<", ">") and s.startswith("(", m.end())):
                self.i = m.end()
                while self.i < self.n and s[self.i] in " \t":
                    self.i += 1
                w = self.word()
                op = m.group(2)
                if op in ("<<", "<<-"):
                    self.heredocs.append(("".join(p for p in w.parts if isinstance(p, str)), op == "<<-", w.quoted))
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
            self.toks.append(("word", w))
            if start_here and not w.quoted and w.text() == "[[":
                self.dbr = True
        return self.toks


def read(src):
    """[Cmd | "(" | ")"] in the order the shell runs them."""
    out, cur = [], Cmd()
    mode, cases, skip_name, func, after_time = None, 0, False, False, False

    def flush():
        nonlocal cur
        if not cur.empty():
            out.append(cur)
        cur = Cmd()

    for tok in Lexer(src).run():
        kind = tok[0]
        if kind == "word":
            w = tok[1]
            t = None if w.quoted else w.text()
            cur.subs += subs_of(w.parts)
            if mode == "header":                          # for NAME in …: a list, not a command
                continue
            if mode == "case":                            # case WORD in
                if t == "in":
                    mode = "pattern"
                continue
            if mode == "pattern":
                if t == "esac":
                    cases -= 1
                    mode = None
                continue
            start = not cur.words and not cur.assigns
            if start and after_time and t == "-p":
                after_time = False
                continue
            after_time = False
            if start and t in RESERVED:
                after_time = t == "time"
                continue
            if start and t in ("for", "select"):
                mode = "header"
                continue
            if start and t == "case":
                cases += 1
                mode = "case"
                continue
            if start and t == "esac" and cases:
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
                continue
            if mode == "case":
                continue
            if v == "(":
                if cur.words and not cur.redirs:          # name ( ): a function definition
                    cur, func = Cmd(), True
                    continue
                flush()
                out.append("(")
                continue
            if v == ")":
                if func:
                    func = False
                    continue
                flush()
                out.append(")")
                continue
            flush()
            if v in (";;", ";&", ";;&") and cases:
                mode = "pattern"
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
