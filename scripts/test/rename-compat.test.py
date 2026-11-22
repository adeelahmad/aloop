#!/usr/bin/env python3
"""The agentic-agile -> aloop rename keeps old names working (no network, no tokens).

  python3 scripts/test/rename-compat.test.py
"""
import importlib.machinery
import importlib.util
import json
import atexit
import os
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True                 # loading libexec/ scripts must not leave __pycache__ there

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
PLUGIN = os.path.join(ROOT, "plugin")
BIN = os.path.join(PLUGIN, "libexec")
T = tempfile.mkdtemp(prefix="aloop-rename-")
atexit.register(shutil.rmtree, T, ignore_errors=True)
ENV = dict(os.environ, HOME=os.path.join(T, "home"), CLAUDE_PLUGIN_DATA=os.path.join(T, "pdata"),
           PYTHONDONTWRITEBYTECODE="1", AGENTIC_NO_AUTOINSTALL="1")
n = 0


def ok(msg):
    global n
    n += 1
    print("ok - " + msg)


def run(*args, cwd=T):
    return subprocess.run(args, cwd=cwd, env=ENV, capture_output=True, text=True)


def load(name, path):
    spec = importlib.util.spec_from_loader(name, importlib.machinery.SourceFileLoader(name, path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# 1. manifests carry the new name
plugin = json.load(open(os.path.join(PLUGIN, ".claude-plugin", "plugin.json")))
market = json.load(open(os.path.join(ROOT, ".claude-plugin", "marketplace.json")))
assert plugin["name"] == "aloop", plugin["name"]
assert market["name"] == "aloop-marketplace" and market["plugins"][0]["name"] == "aloop"
assert os.path.isfile(os.path.join(PLUGIN, "skills", "aloop", "SKILL.md"))
ok("plugin, marketplace and skill are named aloop")
# the plugin directory (claude.ai) needs a square PNG icon, 512-2048 px, under 2 MB
icon = os.path.join(PLUGIN, ".claude-plugin", "icon.png")
with open(icon, "rb") as f:
    head = f.read(24)
assert head[:8] == b"\x89PNG\r\n\x1a\n", "icon.png is not a PNG"
w, h = int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
assert w == h and 512 <= w <= 2048 and os.path.getsize(icon) < 2 * 1024 * 1024, (w, h)
ok("plugin ships a directory-valid icon")
# claude.ai warns on (and strips) angle brackets in a skill description: plugin_upload_skill_md_description_contains_xml
import glob, re as _re
for sk in glob.glob(os.path.join(PLUGIN, "skills", "*", "SKILL.md")):
    fm = open(sk).read().split("---")[1]
    desc = _re.search(r"(?ms)^description:\s*(?:>-?|\|-?)?\s*(.*?)(?=^\S+:|\Z)", fm).group(1)
    assert "<" not in desc and ">" not in desc, (sk, desc)
ok("no skill description holds angle brackets (claude.ai strips them)")

# 2. the legacy CLI names forward to aloop
run("git", "init", "-q")
new, old = run(os.path.join(BIN, "aloop"), "--list"), run(os.path.join(BIN, "agentic"), "--list")
assert new.returncode == 0 and new.stdout == old.stdout and "selfcheck" in new.stdout, (new, old)
assert "agentic" not in new.stdout.split(), new.stdout
ok("agentic --list == aloop --list (aliases hidden)")
for cmd in ((os.path.join(BIN, "agentic"), "init", "--show"), (os.path.join(BIN, "agentic-init"), "--show")):
    r = run(*cmd)
    assert r.returncode == 0 and "aloop project settings" in r.stdout, r
ok("agentic init / agentic-init forward to aloop-init")

# 3. a worker's bind command is recognised under both names
ha = load("host_adapter", os.path.join(BIN, "host-adapter"))
for c in ("aloop bind /w", "agentic bind /w", "cd x && aloop bind /w", "/p/bin/bind /w"):
    m = ha.BIND.search(c)
    assert m and m.group(1) == "/w", c
ok("host-adapter recognises aloop bind and agentic bind")

# 4. installing on OpenCode replaces a pre-rename install instead of loading both
ins = load("aloop_install", os.path.join(BIN, "install"))
oc = os.path.join(T, ".opencode")
legacy = [os.path.join(oc, "plugins", "agentic-agile.js"), os.path.join(oc, "commands", "agentic-init.md"),
          os.path.join(oc, "skills", "agentic-agile", "SKILL.md"), os.path.join(oc, "skills", "agentic-agile-init", "SKILL.md")]
for p in legacy:
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write("<!-- %s -->\n" % ins.LEGACY_MARK)
user_skill = os.path.join(oc, "skills", "agentic-agile-mine", "SKILL.md")
os.makedirs(os.path.dirname(user_skill))
open(user_skill, "w").write("mine\n")
r = run(os.path.join(BIN, "aloop"), "install", "opencode")
assert r.returncode == 0, r
assert not any(os.path.exists(p) for p in legacy), [p for p in legacy if os.path.exists(p)]
assert os.path.isfile(os.path.join(oc, "plugins", "aloop.js")) and os.path.isfile(os.path.join(oc, "skills", "aloop", "SKILL.md"))
assert os.path.isfile(user_skill)
ok("opencode install removes the pre-rename plugin, command and skills (and nothing else)")
lb = os.path.join(ENV["HOME"], ".local", "bin")
assert os.path.realpath(os.path.join(lb, "aloop")) == os.path.realpath(os.path.join(BIN, "aloop"))
assert os.path.islink(os.path.join(lb, "agentic"))
r = run(os.path.join(lb, "agentic"), "--root")
assert r.returncode == 0 and os.path.realpath(r.stdout.strip()) == os.path.realpath(PLUGIN), r
ok("~/.local/bin/aloop and the agentic alias both resolve to this plugin")

# 5. uninstall removes legacy leftovers too
for p in legacy[:2]:
    open(p, "w").write("<!-- %s -->\n" % ins.LEGACY_MARK)
r = run(os.path.join(BIN, "aloop"), "install", "opencode", "--uninstall")
assert r.returncode == 0, r
assert not any(os.path.exists(p) for p in legacy) and not os.path.exists(os.path.join(oc, "plugins", "aloop.js"))
ok("opencode uninstall removes new and pre-rename files")

print("\n%d checks passed" % n)
