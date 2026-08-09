"""browsergraph CLI.

    browsergraph doctor                       what's installed, what's missing
    browsergraph engines                      engines usable right now
    browsergraph dimensions                   the axes and their values
    browsergraph combos --engine playwright   runnable combinations
    browsergraph sample --pairwise            covering-array sample
    browsergraph run graph.yaml               run a graph from config
    browsergraph serve --port 8800            HTTP API
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from browsergraph.dimensions import (
    Behavior,
    Binary,
    Display,
    Engine,
    Identity,
    LLMConfig,
    LLMControl,
    Spec,
    Stealth,
    Transport,
    validate,
)


def _spec_from_env(**over) -> Spec:
    """Build a Spec from environment, so containers configure by env alone."""
    def pick(enum, name, default):
        raw = os.environ.get(name, "")
        try:
            return enum(raw) if raw else default
        except ValueError:
            valid = ", ".join(e.value for e in enum)
            raise SystemExit(f"{name}={raw!r} invalid. Options: {valid}") from None

    ident = Identity(
        profile_dir=os.environ.get("BG_PROFILE_DIR", ""),
        proxy=os.environ.get("BG_PROXY", ""),
        user_agent=os.environ.get("BG_USER_AGENT", ""),
        locale=os.environ.get("BG_LOCALE", "en-US"),
        timezone=os.environ.get("BG_TIMEZONE", ""),
    )
    llm = LLMConfig(
        mode=pick(LLMControl, "BG_LLM_MODE", LLMControl.NONE),
        host=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
        model=os.environ.get("OLLAMA_MODEL", "glm-5.2"),
        api_key=os.environ.get("OLLAMA_API_KEY", ""),
    )
    spec = Spec(
        engine=pick(Engine, "BG_ENGINE", Engine.MOCK),
        binary=pick(Binary, "BG_BINARY", Binary.BUNDLED_CHROMIUM),
        transport=pick(Transport, "BG_TRANSPORT", Transport.LOCAL),
        display=pick(Display, "BG_DISPLAY", Display.HEADLESS),
        stealth=pick(Stealth, "BG_STEALTH", Stealth.NONE),
        behavior=(Behavior.humanlike()
                  if os.environ.get("BG_BEHAVIOR", "") == "humanlike"
                  else Behavior.instant()),
        identity=ident, llm=llm,
        endpoint=os.environ.get("BG_ENDPOINT", ""),
    )
    return spec if not over else type(spec)(**{**spec.to_dict(), **over})


def cmd_doctor(args) -> int:
    from browsergraph.doctor import run_all
    rep = run_all()
    print(rep.text())
    print()
    print("all prerequisites satisfied" if rep.ok
          else "some prerequisites missing — mock engine still works")
    return 0


def cmd_engines(args) -> int:
    from browsergraph.dimensions import ENGINE_REQUIREMENT
    from browsergraph.doctor import available_engines
    usable = set(available_engines())
    for e in Engine:
        mark = "usable " if e in usable else "missing"
        req = ENGINE_REQUIREMENT.get(e, "")
        print(f"{mark}  {e.value:<20} {('pip install ' + req) if req and e not in usable else ''}")
    return 0


def cmd_dimensions(args) -> int:
    for enum in (Engine, Binary, Transport, Display, Stealth, LLMControl):
        print(f"{enum.__name__}:")
        for v in enum:
            print(f"    {v.value}")
    return 0


def cmd_combos(args) -> int:
    from browsergraph.combos import count, enumerate_specs, rejected
    axes = None
    if args.engine:
        axes = {"engine": [Engine(args.engine)], "binary": list(Binary),
                "transport": list(Transport), "display": list(Display),
                "stealth": list(Stealth)}
    total, ok = count(axes)
    print(f"{total} combinations, {ok} runnable, {total - ok} rejected\n")
    for spec in list(enumerate_specs(axes))[: args.limit]:
        print("  " + spec.describe())
    if args.why:
        print("\nrejected:")
        for desc, why in rejected(axes)[: args.limit]:
            print(f"  {desc}\n      {why[0]}")
    return 0


def cmd_sample(args) -> int:
    from browsergraph.sample import coverage, sample_specs
    axes = {"engine": list(Engine), "binary": list(Binary),
            "transport": list(Transport), "display": list(Display),
            "stealth": list(Stealth)}
    specs = sample_specs(axes)
    cov, poss = coverage(axes, specs)
    print(f"{len(specs)} runs cover {cov}/{poss} value-pairs "
          f"({100 * cov / poss:.0f}%)\n")
    for s in specs[: args.limit]:
        print("  " + s.describe())
    return 0


def cmd_run(args) -> int:
    from browsergraph.config import load_graph
    from browsergraph.drivers import build
    from browsergraph.graph import run as run_graph

    graph, spec = load_graph(args.config)
    if args.engine:
        spec = type(spec)(**{**spec.to_dict(), "engine": Engine(args.engine)})
    problems = validate(spec)
    if problems:
        print("spec is not runnable:", "; ".join(problems), file=sys.stderr)
        return 2
    if not args.no_lint:
        from browsergraph.lint import has_errors, lint, report
        findings = lint(graph, spec)
        if findings:
            print(report(findings), file=sys.stderr)
        if has_errors(findings):
            print("refusing to run: fix errors or pass --no-lint", file=sys.stderr)
            return 2

    result = run_graph(graph, spec, build(spec))
    print(result.summary())
    for line in result.log:
        print("   " + line)
    if args.json:
        print(json.dumps({"ok": result.ok, "data": result.context.data,
                          "artifacts": result.context.artifacts}, default=str))
    return 0 if result.ok else 1


def cmd_lint(args) -> int:
    from browsergraph.config import load_graph
    from browsergraph.lint import has_errors, lint, report
    graph, spec = load_graph(args.config)
    findings = lint(graph, spec)
    print(report(findings))
    return 1 if has_errors(findings) else 0


def cmd_tasks(args) -> int:
    from browsergraph.tasks import catalog
    for t in catalog():
        print(f"{t['name']:<14} {t['summary']}")
        if args.verbose:
            for prm in t["params"]:
                req = "required" if prm.get("required", True) else f"default={prm.get('default')}"
                print(f"     --{prm['name']:<22} {prm.get('type','str'):<6} {req}")
    return 0


def cmd_task(args) -> int:
    """Run a task by name: browsergraph task research --url https://x.example"""
    import json as _json

    from browsergraph.drivers import build
    from browsergraph.logging import configure, log, new_run
    from browsergraph.tasks import make as make_task

    configure()
    new_run(args.name)
    spec = _spec_from_env()
    values = dict(kv.split("=", 1) for kv in (args.set or []))
    if args.url:
        values["url"] = args.url

    task = make_task(args.name, **values)
    log.info("task.start", task=args.name, spec=spec.describe(), params=values)
    result = task.run(build(spec))
    log.info("task.done", task=args.name, ok=result.ok,
             pages=len(result.pages_visited), elapsed=round(result.elapsed, 2))
    print(_json.dumps(result.to_dict(), indent=2, default=str))
    return 0 if result.ok else 1


def cmd_plugins(args) -> int:
    from browsergraph.plugins import discover, load
    found = discover(*(args.dir or []))
    if not found:
        print("no plugins found "
              "(set BROWSERGRAPH_PLUGIN_PATH or pass --dir)")
        return 0
    for m in found:
        print(f"{m.name:<20} v{m.version:<8} {m.description[:50]}")
        for cap, items in m.provides.items():
            if items:
                print(f"     {cap}: {', '.join(items)}")
        if args.load:
            rep = load(m, allow_override=args.allow_override)
            status = "loaded" if rep.loaded else f"FAILED: {rep.error}"
            print(f"     -> {status}")
            if rep.undeclared:
                print(f"     -> undeclared: {rep.undeclared}")
    return 0


def cmd_envs(args) -> int:
    """Manage per-engine isolated environments."""
    from browsergraph.isolate import ISOLATED_FAMILIES, Env, list_envs

    if args.action == "list":
        existing = {e.name: e for e in list_envs()}
        for name in sorted(set(ISOLATED_FAMILIES) | set(existing)):
            env = existing.get(name) or Env(name=name)
            mark = "ready  " if env.exists else "absent "
            pkgs = ", ".join(ISOLATED_FAMILIES.get(name, []))
            print(f"{mark} {name:<14} {env.path}")
            if pkgs and not env.exists:
                print(f"          installs: {pkgs}")
        return 0

    if not args.name:
        print("--name is required for create/remove", file=sys.stderr)
        return 2
    env = Env(name=args.name)
    if args.action == "create":
        print(f"creating {env.path} …")
        env.create(with_browsers=not args.no_browsers)
        print("ready:", env.exists)
        return 0
    if args.action == "remove":
        env.remove()
        print("removed", env.path)
        return 0
    return 2


def cmd_serve(args) -> int:
    from browsergraph.server import serve
    serve(port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="browsergraph", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="check prerequisites").set_defaults(fn=cmd_doctor)
    sub.add_parser("engines", help="which engines are usable").set_defaults(fn=cmd_engines)
    sub.add_parser("dimensions", help="list axes and values").set_defaults(fn=cmd_dimensions)

    c = sub.add_parser("combos", help="enumerate runnable combinations")
    c.add_argument("--engine")
    c.add_argument("--limit", type=int, default=20)
    c.add_argument("--why", action="store_true", help="show rejection reasons")
    c.set_defaults(fn=cmd_combos)

    s = sub.add_parser("sample", help="pairwise covering sample")
    s.add_argument("--pairwise", action="store_true", default=True)
    s.add_argument("--limit", type=int, default=30)
    s.set_defaults(fn=cmd_sample)

    r = sub.add_parser("run", help="run a graph from a YAML/JSON config")
    r.add_argument("config")
    r.add_argument("--engine")
    r.add_argument("--json", action="store_true")
    r.add_argument("--no-lint", action="store_true", help="skip pre-run checks")
    r.set_defaults(fn=cmd_run)

    li = sub.add_parser("lint", help="static checks on a graph config")
    li.add_argument("config")
    li.set_defaults(fn=cmd_lint)

    tl = sub.add_parser("tasks", help="list available tasks")
    tl.add_argument("-v", "--verbose", action="store_true")
    tl.set_defaults(fn=cmd_tasks)

    tk = sub.add_parser("task", help="run a task by name")
    tk.add_argument("name")
    tk.add_argument("--url")
    tk.add_argument("--set", action="append", metavar="KEY=VALUE")
    tk.set_defaults(fn=cmd_task)

    pl = sub.add_parser("plugins", help="discover and inspect plugins")
    pl.add_argument("--dir", action="append")
    pl.add_argument("--load", action="store_true")
    pl.add_argument("--allow-override", action="store_true")
    pl.set_defaults(fn=cmd_plugins)

    ev = sub.add_parser("envs", help="isolated per-engine environments")
    ev.add_argument("action", choices=["list", "create", "remove"])
    ev.add_argument("--name")
    ev.add_argument("--no-browsers", action="store_true")
    ev.set_defaults(fn=cmd_envs)

    v = sub.add_parser("serve", help="HTTP API")
    v.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8800)))
    v.set_defaults(fn=cmd_serve)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
