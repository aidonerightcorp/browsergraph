"""Engine isolation — conflicting engines in separate environments.

Some engines cannot share a virtualenv. Camoufox pins its own Playwright
build; undetected-chromedriver wants a Chrome major that Selenium Manager may
not have fetched; a future engine will pin something else again. Installing
them together means the last one installed wins and silently breaks the rest.

The answer is not to drop engines, it is to stop pretending one environment
must hold them all. Each engine family gets its own venv, and the adapter runs
in a worker process there, speaking `BrowserPort` over a line-delimited JSON
protocol. The caller sees an ordinary `BrowserPort`.

Costs, honestly: a process hop per call (~0.2-1ms locally) and disk per env.
That is worth paying for engines that genuinely conflict, and pointless for
ones that do not — so isolation is opt-in per family, never the default.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import venv
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_ROOT = Path(os.environ.get(
    "BROWSERGRAPH_ENV_ROOT",
    Path.home() / ".cache" / "browsergraph" / "envs"))

#: Engine families that are known to conflict when co-installed, with the
#: packages each needs. Anything absent here runs in-process.
ISOLATED_FAMILIES: dict[str, list[str]] = {
    "camoufox": ["camoufox[geoip]"],
    "playwright": ["playwright"],
    "patchright": ["patchright"],
    "selenium": ["selenium", "undetected-chromedriver", "setuptools<81"],
    "seleniumbase": ["seleniumbase"],
    "nodriver": ["nodriver"],
}

#: Post-install browser fetch, where the package needs one.
POST_INSTALL: dict[str, list[list[str]]] = {
    "camoufox": [["-m", "camoufox", "fetch"]],
    "playwright": [["-m", "playwright", "install", "chromium"]],
    "patchright": [["-m", "patchright", "install", "chromium"]],
}


class IsolationError(RuntimeError):
    pass


@dataclass
class Env:
    """A per-family virtualenv."""
    name: str
    root: Path = DEFAULT_ROOT
    packages: list[str] = field(default_factory=list)

    @property
    def path(self) -> Path:
        return Path(self.root) / self.name

    @property
    def python(self) -> Path:
        return self.path / ("Scripts" if os.name == "nt" else "bin") / "python"

    @property
    def exists(self) -> bool:
        return self.python.exists()

    def create(self, with_browsers: bool = True, timeout: float = 900) -> Env:
        """Create the venv and install this family's packages. Idempotent."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.exists:
            venv.EnvBuilder(with_pip=True, symlinks=True).create(str(self.path))

        pkgs = self.packages or ISOLATED_FAMILIES.get(self.name, [])
        if pkgs:
            self._run([str(self.python), "-m", "pip", "install", "-q",
                       "--upgrade", "pip"], timeout=timeout)
            self._run([str(self.python), "-m", "pip", "install", "-q", *pkgs],
                      timeout=timeout)

        # the worker imports browsergraph itself, so the package must be present
        self._run([str(self.python), "-m", "pip", "install", "-q", "-e",
                   str(_package_root())], timeout=timeout)

        if with_browsers:
            for args in POST_INSTALL.get(self.name, []):
                self._run([str(self.python), *args], timeout=timeout, check=False)
        return self

    def remove(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)

    def _run(self, cmd: list[str], timeout: float, check: bool = True) -> None:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.SubprocessError) as e:
            raise IsolationError(f"{' '.join(cmd[:3])}…: {type(e).__name__}: {e}") from e
        if check and proc.returncode != 0:
            raise IsolationError(
                f"{' '.join(cmd[:4])}… failed ({proc.returncode}): "
                f"{(proc.stderr or proc.stdout or '')[-400:]}")

    def report(self) -> dict:
        return {"name": self.name, "path": str(self.path), "exists": self.exists,
                "python": str(self.python) if self.exists else ""}


def _package_root() -> Path:
    return Path(__file__).resolve().parent.parent


def env_for(engine, root: Path | None = None) -> Env:
    """The environment an engine should run in."""
    from browsergraph.dimensions import ENGINE_FAMILY, Engine

    engine = Engine(engine)
    # Camoufox conflicts with the shared playwright install, so it is keyed by
    # engine rather than by family.
    name = "camoufox" if engine is Engine.CAMOUFOX else ENGINE_FAMILY.get(engine, "")
    if engine.value in ISOLATED_FAMILIES:
        name = engine.value
    return Env(name=name or engine.value, root=root or DEFAULT_ROOT)


def list_envs(root: Path | None = None) -> list[Env]:
    base = Path(root or DEFAULT_ROOT)
    if not base.exists():
        return []
    return [Env(name=p.name, root=base) for p in sorted(base.iterdir()) if p.is_dir()]


# --- worker protocol --------------------------------------------------------

def encode(msg: dict) -> bytes:
    return (json.dumps(msg, default=str) + "\n").encode()


def decode(line: bytes) -> dict:
    return json.loads(line.decode() or "{}")


class Worker:
    """A child process running one browser adapter inside an isolated venv."""

    def __init__(self, env: Env, spec_dict: dict, timeout: float = 120.0) -> None:
        self.env = env
        self.spec_dict = spec_dict
        self.timeout = timeout
        self.proc: subprocess.Popen | None = None

    def start(self) -> None:
        if not self.env.exists:
            raise IsolationError(
                f"environment {self.env.name!r} does not exist at {self.env.path}. "
                f"Create it with: browsergraph envs create {self.env.name}")
        self.proc = subprocess.Popen(
            [str(self.env.python), "-m", "browsergraph.worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={**os.environ, "PYTHONUNBUFFERED": "1"})
        reply = self.call("open", spec=self.spec_dict)
        if not reply.get("ok"):
            raise IsolationError(f"worker failed to open browser: {reply.get('error')}")

    def call(self, op: str, **kwargs) -> dict:
        if self.proc is None or self.proc.poll() is not None:
            raise IsolationError("worker is not running")
        stdin, stdout = self.proc.stdin, self.proc.stdout
        if stdin is None or stdout is None:      # only if spawned without pipes
            raise IsolationError("worker has no stdio pipes")
        try:
            stdin.write(encode({"op": op, **kwargs}))
            stdin.flush()
            line = stdout.readline()
        except (BrokenPipeError, OSError) as e:
            err = (self.proc.stderr.read() or b"").decode()[-400:] if self.proc.stderr else ""
            raise IsolationError(f"worker died during {op}: {e}. {err}") from e
        if not line:
            err = (self.proc.stderr.read() or b"").decode()[-400:] if self.proc.stderr else ""
            raise IsolationError(f"worker closed the pipe during {op}. {err}")
        return decode(line)

    def stop(self) -> None:
        if self.proc is None:
            return
        try:
            if self.proc.poll() is None:
                self.call("close")
                if self.proc.stdin is not None:
                    self.proc.stdin.close()
                self.proc.wait(timeout=15)
        except Exception:
            pass
        finally:
            if self.proc.poll() is None:
                self.proc.kill()
            self.proc = None
