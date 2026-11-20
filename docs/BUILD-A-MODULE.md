# Build your own module

*Your host harness (Claude Code, Codex, OpenCode, dsh) is the hardware. aloop is the kernel. **Modules**
plug into the kernel, and a **distro** is a full user-space bundle for one domain. aloop ships one
distro, `software`, which is the test-first loop. This page shows how to use existing modules, build
your own, and bundle them into a distro for your kind of work, such as marketing, writing, research or ops.*

> *Models explore. Gates decide.* A module decides what "done" means for your work, and the kernel
> makes sure that decision is enforced.

## The model

| Layer | What it holds | Who changes it | Where |
|---|---|---|---|
| **Hardware** | The host harness that runs the models and tools | its vendor | Claude Code, Codex, OpenCode, dsh |
| **Kernel** | The gate engine, the core gates (scope, door, plan, final), the audit log, and the rule that whatever a project declares actually runs and is logged | aloop releases only | the plugin |
| **Modules** | Your own checks, added next to the core gates | you, or anyone you take one from | `~/.aloop/modules/<name>/` |
| **User space** | The loop's opinions: roles, prompts and skills, plus `distro.json` | you, between runs | `docs/agents/userspace/` (in git), with an optional personal copy in `~/.aloop/projects/` |
| **Distro** | A shareable bundle for a domain: modules, roles and skills | anyone | any folder or git repo |

A module can **add** checks. A distro can add modules and **re-shape** roles. Neither can remove or
weaken a core gate, and neither can add a role that runs without gates. Those limits are what make
them safe to share.

## 1. Build a module

    aloop module new no-banned-words --on SubagentStop:green-worker --on Stop

This creates `~/.aloop/modules/no-banned-words/` with three files:

    module.json     when it runs, and what it runs
    check           the check: a commented example that works as written
    README.md

`module.json`:

```json
{
  "name": "no-banned-words",
  "description": "refuses copy with banned words",
  "on": ["SubagentStop:green-worker", "Stop"],
  "command": "./check",
  "timeout": 120
}
```

`on` names the events the module runs on:

- `SubagentStop` runs after any role finishes.
- `SubagentStop:<role>` runs after one role finishes, for example `green-worker` or `planner`.
- `Stop` runs when the session tries to stop.

`check` is any executable. It runs from the project root, gets the hook payload on stdin, and has
these variables set: `ALOOP_EVENT`, `ALOOP_ROLE`, `ALOOP_PROJECT`, `ALOOP_DISTRO` and `ALOOP_MODULE`.

- **Exit 0** passes.
- **Exit 2** blocks. Whatever the check writes to stderr is handed back to the agent as the reason.
- **Any other exit, a crash or a timeout** also blocks. A check the project declared always runs, or
  the run stops.

```bash
#!/usr/bin/env bash
cd "$ALOOP_PROJECT" || exit 2
if git diff --name-only HEAD -- 'copy/*.md' | xargs -r grep -l -i 'synergy'; then
  echo "no-banned-words: copy says 'synergy' (banned word list: docs/brand.md)" >&2
  exit 2
fi
```

Ideas for modules: fact-checking against cited sources, brand voice, reading level, broken links,
legal disclaimers, image alt text, word counts per channel, a license header, a performance budget.

## 2. Add it to a project

    aloop module add no-banned-words
    aloop module list           # added / CHANGED since added / missing

`add` pins the module's sha256 in `docs/agents/userspace/distro.json`, which you commit. If you change
the module later, the kernel refuses to run it until you look at the change and run `add` again.
Every run is written to the audit log as a `module.run` event, with the module's sha and its verdict.

## 3. Bundle a distro (optional)

When a domain needs several modules and re-shaped roles, bundle them:

    aloop distro new marketing --dir ~/distros

This creates:

    marketing/
      distro.json                {"name": "marketing", "description": "..."}
      modules/marketing-check/   a working example module
      userspace/                 optional roles and skills
      README.md

To re-shape a role, copy it from a project's `docs/agents/userspace/agents/<role>.md` into the
distro's `userspace/agents/<role>.md` and rewrite it. For example, the planner can plan campaigns
instead of code, and the green worker can write copy instead of functions. The role's name stays the
same, so its gates stay the same.

    aloop distro check ~/distros/marketing       # validate without installing
    cd my-project
    aloop distro install ~/distros/marketing     # copies and adds its modules, copies its roles,
                                                 # makes it the project's distro
    aloop distro                                 # marketing, and the modules it runs

`install` keeps any role you have already customized (`--force` replaces it), and it refuses to
overwrite an installed module that differs from the distro's copy (`--force` replaces that too).

## 4. Share it

Modules and distros are just folders, so you can publish them as git repos. Other people clone one and
run `aloop distro install <dir>`, or copy a module into `~/.aloop/modules/` and run `aloop module add`.
Their own customizations are never overwritten, and nothing in a module or a distro can switch off a
core gate.

## Reference

    aloop module new <name> [--on EVENT]...  |  aloop module add|remove <name>  |  aloop module list
    aloop distro  |  aloop distro use <name>  |  aloop distro new <name> [--dir D]
    aloop distro check <dir>  |  aloop distro install <dir> [--force]
    aloop userspace status|diff|merge|accept|reset <rel>

Design and status: [ROADMAP §4.15](ROADMAP.md) (M12). Tests: `scripts/test/modules.test.py`,
`scripts/test/userspace.test.py`.
