#!/usr/bin/env python3
"""ensure-tools rebuilds the bundled md-db / ctx-symbols when their SOURCE changes, not only
when the plugin version moves. Regression (live e2e run 9): a ctx-symbols fix shipped without
a version bump, every session kept the old binary, and the fixed false positive still fired.

Uses a fake `cargo` (no real build):  python3 scripts/test/ensure-tools.test.py
"""
import os
import shutil
import subprocess
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.join(HERE, "..", "..", "plugin")
T = tempfile.mkdtemp(prefix="aloop-ensure-")
ROOT = os.path.join(T, "plugin")
n = 0


def ok(msg):
    global n
    n += 1
    print("ok - " + msg)


os.makedirs(os.path.join(ROOT, "bin"))
shutil.copy(os.path.join(PLUGIN, "bin", "ensure-tools"), os.path.join(ROOT, "bin"))
os.makedirs(os.path.join(ROOT, ".claude-plugin"))
open(os.path.join(ROOT, ".claude-plugin", "plugin.json"), "w").write('{"version": "9.9.9"}\n')
for crate in ("ctx-symbols", "md-db"):
    os.makedirs(os.path.join(ROOT, "tools", crate, "src"))
    open(os.path.join(ROOT, "tools", crate, "Cargo.toml"), "w").write('[package]\nname = "%s"\n' % crate)
    open(os.path.join(ROOT, "tools", crate, "src", "main.rs"), "w").write("fn main() {}\n")

fake = os.path.join(T, "fakebin")
os.makedirs(fake)
open(os.path.join(fake, "cargo"), "w").write(
    '#!/bin/sh\nmkdir -p "$CARGO_TARGET_DIR/release"\nn="$(basename "$PWD")"\n'
    'printf "#!/bin/sh\\n" > "$CARGO_TARGET_DIR/release/$n"; chmod +x "$CARGO_TARGET_DIR/release/$n"\n'
    'echo built >> "%s/builds"\n' % T)
os.chmod(os.path.join(fake, "cargo"), 0o755)
ENV = dict(os.environ, CLAUDE_PLUGIN_DATA=os.path.join(T, "data"), HOME=T,
           PATH=fake + ":/usr/bin:/bin")


def sync():
    r = subprocess.run([os.path.join(ROOT, "bin", "ensure-tools"), "--sync"], env=ENV,
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r
    return open(os.path.join(T, "builds")).read().count("built") if os.path.exists(os.path.join(T, "builds")) else 0


assert sync() == 2
ok("first sync builds both tools")
assert sync() == 2
ok("an unchanged source tree is not rebuilt")
open(os.path.join(ROOT, "tools", "ctx-symbols", "src", "main.rs"), "a").write("// fix\n")
assert sync() == 4, "source change did not trigger a rebuild"
ok("a source change without a version bump rebuilds")
assert sync() == 4
ok("and is then current again")

# Another aloop checkout's bin/ ahead on PATH (an older install, ~/work/agentic-agile/plugin/bin)
# holds only SHIMS of the same names: resolving to one made the gate exec a shim whose own
# ensure-tools resolved back here, and `make conformance` hung (found on a Mac release run).
other = os.path.join(T, "other", "plugin", "bin")
os.makedirs(other)
for name in ("ensure-tools", "ctx-symbols", "md-db"):
    shutil.copy(os.path.join(PLUGIN, "bin", name), other)
envo = dict(ENV, CLAUDE_PLUGIN_DATA=os.path.join(T, "data-empty"), PATH=other + ":" + ENV["PATH"])
r = subprocess.run([os.path.join(ROOT, "bin", "ensure-tools"), "--resolve", "ctx-symbols"], env=envo,
                   capture_output=True, text=True, timeout=20)
assert other not in r.stdout, ("resolved to another checkout's shim", r.stdout)
real = os.path.join(T, "realbin")
os.makedirs(real)
open(os.path.join(real, "ctx-symbols"), "w").write("#!/bin/sh\n")
os.chmod(os.path.join(real, "ctx-symbols"), 0o755)
envo["PATH"] = other + ":" + real + ":" + ENV["PATH"]
r = subprocess.run([os.path.join(ROOT, "bin", "ensure-tools"), "--resolve", "ctx-symbols"], env=envo,
                   capture_output=True, text=True, timeout=20)
assert r.returncode == 0 and r.stdout.strip() == os.path.join(real, "ctx-symbols"), r
ok("--resolve skips another plugin checkout's shims on PATH and finds the real binary behind them")

shutil.rmtree(T, ignore_errors=True)
print("\n%d checks passed" % n)
