"""Journey runner for the to-do app: a user adds a task and sees it in their list straight away.

Command (run from the project root, as `aloop mission` runs a journey):

    python3 docs/agents/mission/journeys/runners/todo_journey.py

Steps, in order (the journey's step ids):
    open   a new to-do list opens, empty
    add    the user adds the task "buy milk"
    see    "buy milk" is in their list straight away

Metrics: none. The journey passes when every step passes (pass criterion `steps: all`).
Pin: python3, probed with `python3 --version`; pin exactly what that probe prints.

The last stdout line is the result: {"steps": {"open": …, "add": …, "see": …}, "metrics": {}}.
The runner writes no files (no bytecode either), so two runs on the same code agree. It looks
nothing up beside itself: its own dir leaves sys.path before the first import, and the project
root is added only after the standard library is imported, so a file planted next to it (or a
json.py in the root) never stands in for the code it measures with.
"""
import sys
import os  # loaded with the interpreter (site imports it): never looked up on sys.path

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path[:] = [p for p in sys.path if os.path.realpath(p or os.curdir) != HERE]
sys.dont_write_bytecode = True
import json  # noqa: E402 - only once this dir is off sys.path, before the root is on it

ROOT = os.path.realpath(os.path.join(HERE, *[os.pardir] * 5))
sys.path.insert(0, ROOT)


def main():
    steps = {"open": False, "add": False, "see": False}
    try:
        import todo
        tasks = todo.TodoList()
        steps["open"] = tasks.tasks() == []
        tasks.add("buy milk")
        steps["add"] = True
        steps["see"] = "buy milk" in tasks.tasks()
    except Exception as e:   # a crash fails every step it did not reach
        print("journey stopped: %s: %s" % (type(e).__name__, e), file=sys.stderr)
    print(json.dumps({"steps": steps, "metrics": {}}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
