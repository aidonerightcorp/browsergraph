"""Run a node in its own process, with a clock and a ceiling on it.

`execute.run` calls your functions in this process. That is right for almost
everything and wrong for one case: a step that can hang, or allocate until the
machine dies. Both have happened here — a search asked to enumerate 3.8 trillion
routes took a 61GB box to its knees, and the only thing that stopped it was
somebody watching.

So a step can be given a subprocess of its own, with a wall clock and a memory
ceiling. When it exceeds either, the child dies and the step fails with a
message naming which limit it hit. The rest of the plan carries on, or stops,
according to the same rules as any other failure.

**This is lifecycle isolation, not a sandbox.** The distinction matters enough
to put in the first paragraph a reader is likely to skim:

* It bounds *time* and *memory*, so a runaway step is contained.
* It does **not** contain a hostile one. The child shares the filesystem, the
  network and the user account. It is handed arguments through `pickle`, which
  executes code by design.
* So it is safe for *your* code failing in ways you did not predict, and no
  protection at all against code you did not write. Running a model's generated
  node inside this would be a mistake, and calling it a sandbox is how somebody
  ends up making it.

A real boundary needs a separate kernel or a separate machine. That is a
different piece of work and it is not this one.

One practical trap, because it costs an hour every time. When `spawn` is used
the child **re-imports the module it was started from**, so a script that calls
a bounded step at import time runs its whole self once per child:

    def main():
        run_the_thing()

    if __name__ == "__main__":      # required, not style
        main()

Without that guard the output interleaves, the timings are nonsense, and the
failure looks like the library rather than the script. From a notebook this does
not arise, because `fork` is used there instead.
"""
from __future__ import annotations

import multiprocessing
import pickle
import resource
import sys
import traceback
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


class LimitExceeded(RuntimeError):
    """The child hit a wall clock or a memory ceiling and was stopped."""


@dataclass(frozen=True)
class Limits:
    """What a bounded step is allowed to spend."""
    seconds: float = 30.0
    memory_mb: int = 2048
    #: Applied inside the child before the function runs. Address space rather
    #: than resident size, because the allocation is what has to fail — killing
    #: a process after it has already taken the memory is too late to help the
    #: machine it was taking it from.
    address_space: bool = True

    def describe(self) -> str:
        return f"{self.seconds:g}s, {self.memory_mb}MB"


def _start_method() -> str:
    """`spawn` where it works, `fork` where `spawn` cannot bootstrap.

    `spawn` re-imports `__main__` in the child. From a script that is fine;
    from a notebook, a REPL or a piped heredoc there is no file to import and
    every child dies before it runs a line — which looks exactly like the
    memory ceiling firing, and cost me a while to tell apart.

    `fork` has its own hazard: a forked child inherits locks held by threads
    that do not exist in it. For a step that is about to be replaced by its
    own function call that is an acceptable risk, and it is the only way this
    works in the place it is most useful — a notebook.
    """
    main = sys.modules.get("__main__")
    if getattr(main, "__file__", None):
        return "spawn"
    return "fork" if "fork" in multiprocessing.get_all_start_methods() else "spawn"


def _child(function_bytes: bytes, kwargs_bytes: bytes, limits: Limits,
           pipe) -> None:
    """Runs in the subprocess. Sets its own limits, then does the work."""
    try:
        if limits.address_space and limits.memory_mb:
            cap = limits.memory_mb * 1024 * 1024
            soft, hard = resource.getrlimit(resource.RLIMIT_AS)
            if hard != resource.RLIM_INFINITY:
                cap = min(cap, hard)
            resource.setrlimit(resource.RLIMIT_AS, (cap, hard))
    except (ValueError, OSError):
        # A platform that will not take the limit is a platform where this
        # degrades to a plain subprocess. Better than refusing to run.
        pass

    try:
        function = pickle.loads(function_bytes)
        kwargs = pickle.loads(kwargs_bytes)
        pipe.send(("ok", pickle.dumps(function(**kwargs))))
    except MemoryError:
        pipe.send(("limit", f"ran out of memory (ceiling {limits.memory_mb}MB)"))
    except BaseException:                          # noqa: BLE001 - reported home
        pipe.send(("error", traceback.format_exc().strip().splitlines()[-1]))
    finally:
        pipe.close()


def bound(function: Callable, limits: Limits | None = None) -> Callable:
    """Wrap a callable so each call runs in its own bounded subprocess.

    The wrapper keeps the original signature, so `execute.run` cannot tell the
    difference — which is the point. Bounding a step is a decision about *how*
    it runs, and nothing about the graph should have to change for it.

    The function and its arguments cross by `pickle`, so both have to be
    picklable. A closure over a notebook local will not be; a module-level
    function will. That constraint is real and worth hitting early rather than
    discovering in production, so it raises rather than silently falling back to
    running in-process.
    """
    limits = limits or Limits()

    def call(**kwargs: Any) -> Any:
        try:
            payload = pickle.dumps(function)
        except (pickle.PicklingError, AttributeError, TypeError) as problem:
            raise ValueError(
                f"{getattr(function, '__name__', function)!r} cannot be sent to "
                f"a subprocess ({problem}). Bounded steps need a module-level "
                f"function, not a closure or a lambda.") from problem

        context = multiprocessing.get_context(_start_method())
        parent, child = context.Pipe(duplex=False)
        process = context.Process(
            target=_child,
            args=(payload, pickle.dumps(kwargs), limits, child))
        process.start()
        child.close()

        result = None
        if parent.poll(limits.seconds):
            try:
                result = parent.recv()
            except EOFError:
                result = None
        process.join(timeout=1.0)

        if process.is_alive():
            process.kill()
            process.join()
            raise LimitExceeded(
                f"took longer than {limits.seconds:g}s and was stopped")
        parent.close()

        if result is None:
            # No message and not alive. Two very different causes, and naming
            # only one of them sent me looking for a memory problem when the
            # child had actually failed to start: `spawn` re-imports `__main__`,
            # and from a notebook or a piped script there is no file to import.
            raise LimitExceeded(
                f"the step produced no result and its process is gone "
                f"(limits: {limits.describe()}). Either it hit the memory "
                f"ceiling, or the subprocess could not start — check that the "
                f"function is importable from a module.")

        kind, payload = result
        if kind == "ok":
            return pickle.loads(payload)
        if kind == "limit":
            raise LimitExceeded(payload)
        raise RuntimeError(payload)

    call.__name__ = f"bounded_{getattr(function, '__name__', 'call')}"
    call.__doc__ = (f"Runs in a subprocess bounded to {limits.describe()}. "
                    f"Lifecycle isolation, not a security sandbox.")
    return call


def bounded_runtime(functions: Mapping[str, Callable],
                    limits: Limits | None = None,
                    only: Mapping[str, Limits] | None = None):
    """An `execute.Runtime` where some or all steps are bounded.

    `only` gives per-candidate limits and, by naming candidates, restricts
    bounding to them. Most steps do not need a subprocess and paying ~50ms of
    spawn cost for each one is a poor trade; the ones that can hang do.
    """
    from browsergraph.execute import Runtime

    chosen = dict(only or {})
    out = {}
    for candidate, function in functions.items():
        if only is None:
            out[candidate] = bound(function, limits)
        elif candidate in chosen:
            out[candidate] = bound(function, chosen[candidate] or limits)
        else:
            out[candidate] = function
    return Runtime(out)


def available() -> bool:
    """Whether bounded execution can work here.

    `spawn` needs to be able to re-import the parent, and `RLIMIT_AS` is not
    meaningful on every platform. Checked rather than assumed so a caller can
    fall back deliberately instead of discovering it at the first failure.
    """
    if sys.platform.startswith("win"):
        return False
    try:
        multiprocessing.get_context("spawn")
        resource.getrlimit(resource.RLIMIT_AS)
    except (ValueError, OSError, AttributeError):
        return False
    return True
