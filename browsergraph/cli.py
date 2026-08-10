"""browsergraph CLI.

    browsergraph doctor                       what's installed, what's missing
    browsergraph engines                      engines usable right now
    browsergraph dimensions                   the axes and their values
    browsergraph combos --engine playwright   runnable combinations
    browsergraph sample --pairwise            covering-array sample
    browsergraph run graph.yaml               run a graph from config
    browsergraph serve --port 8800            HTTP API
    browsergraph bootstrap                    get a working browser, whatever it takes
    browsergraph space --html space.html      every dimension and every path
    browsergraph planes --html planes.html    task planes, candidates, routes
    browsergraph nodes [--json]               every node kind, its contract or manifest
    browsergraph graph graph.yaml --mermaid   draw a graph, audit its contracts
    browsergraph workbench -o studio.html     stages, candidates, routes, feedback
    browsergraph fetch chromedriver           download a browser or driver
    browsergraph route --compare              propose a route; show the search
    browsergraph check my-graph.json          is this a valid graph solution?
    browsergraph evidence --suggest           a route worth trying, from evidence
    browsergraph compile learned --against cheapest   the plan, and what changed
    browsergraph capabilities                 what each engine can actually do
    browsergraph models                       which model for which job, and why
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

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
    axes: dict[str, list[Any]] | None = None
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
    axes: dict[str, list[Any]] = {"engine": list(Engine), "binary": list(Binary),
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


def cmd_bootstrap(args) -> int:
    """Install whatever is needed until a browser actually launches."""
    from browsergraph.bootstrap import ensure_browser
    rep = ensure_browser(Engine(args.engine), install=not args.no_install,
                         apt=not args.no_apt, verbose=True)
    print()
    print(rep.text())
    return 0 if rep.ok else 1


def cmd_workbench(args) -> int:
    """Render the stage/candidate/route studio as self-contained HTML."""
    from browsergraph.workbench import WorkbenchDefinition

    if args.config:
        bench = WorkbenchDefinition.load(args.config)
    else:
        from browsergraph.demo import workbench as demo_workbench
        bench = demo_workbench()

    problems = bench.validate()
    if problems:
        print("workbench does not validate:")
        for problem in problems[:20]:
            print("  " + problem)
        if len(problems) > 20:
            print(f"  ... and {len(problems) - 20} more")
        return 1

    if args.export_data:
        bench.write_json(args.export_data)
        print(f"wrote {args.export_data}")
    if args.suite:
        written = bench.write_suite(args.suite)
        print(f"wrote {len(written)} files to {args.suite}")
        for path in written:
            print("  " + path)
        return 0

    bench.write_html(args.out, view=args.view)
    print(f"wrote {args.out}  ({bench.summary()})")
    return 0


def cmd_verify(args) -> int:
    """Run every route of a workbench and report what actually happened.

    `check` validates a description. `execute` runs one route. Neither answers
    the question you have before shipping: *does all of this still work?*

    Negative controls are the part worth having. A verifier that only reports
    successes cannot tell "everything passed" from "nothing ran" — so a route
    expected to fail and passing is reported as a failure of the verifier, not
    a happy surprise.
    """
    import importlib
    import itertools

    from browsergraph import execute as _execute
    from browsergraph.compile import CompileError, compile_route
    from browsergraph.demo import workbench as demo_workbench
    from browsergraph.workbench import WorkbenchDefinition

    bench = (WorkbenchDefinition.load(args.config) if args.config
             else demo_workbench())

    runtime = _execute.Runtime()
    if args.runtime:
        module_name, _, attribute = args.runtime.partition(":")
        found = getattr(importlib.import_module(module_name),
                        attribute or "RUNTIME")
        runtime = (found if isinstance(found, _execute.Runtime)
                   else _execute.Runtime(found))

    expected_failures = set(args.expect_failure or ())
    leaves = [s for s in bench.leaf_stages if s.candidates]
    pools = [list(s.candidates[:args.per_stage]) for s in leaves]
    total = 1
    for pool in pools:
        total *= len(pool)

    if total > args.limit:
        print(f"{total:,} route combinations exceeds the {args.limit:,} limit; "
              f"lower --per-stage or raise --limit")
        return 1

    print(f"{bench.title}: verifying {total:,} route(s)")
    passed = failed = controls = surprises = 0

    for combo in itertools.product(*pools):
        route = {stage.id: cid for stage, cid in zip(leaves, combo, strict=True)}
        label = ",".join(f"{k}={v}" for k, v in sorted(route.items()))
        control = any(cid in expected_failures for cid in combo)
        try:
            plan = compile_route(bench, route)
        except CompileError as problem:
            if control:
                controls += 1
                continue
            failed += 1
            print(f"  FAIL compile  {label[:70]}")
            print(f"       {problem.problems[0]}")
            continue

        result = _execute.run(plan, runtime, strict=True)
        if result.ok and not control:
            passed += 1
        elif not result.ok and control:
            controls += 1
        elif not result.ok:
            failed += 1
            bad = next((s for s in result.steps if not s.ok), None)
            print(f"  FAIL run      {label[:70]}")
            if bad:
                print(f"       {bad.stage}: {bad.error[:90]}")
        else:
            # Expected to fail and did not. Reported as a verifier failure:
            # a control that stops controlling is how a suite quietly starts
            # proving nothing.
            surprises += 1
            print(f"  SURPRISE      {label[:70]}")
            print("       expected this route to fail, and it passed")

    print(f"\n{passed} passed, {controls} negative control(s) failed as "
          f"intended, {failed} unexpected failure(s), {surprises} surprise(s)")
    if not passed and not controls:
        print("nothing ran — a verifier that reports no failures because it "
              "attempted nothing is worse than none")
        return 1
    return 0 if not failed and not surprises else 1


def cmd_execute(args) -> int:
    """Run a compiled plan against real functions.

    `run` was already taken by the browser graph runner, so this is `execute`.
    The two are different things: `run` drives a browser through a node graph,
    this runs any compiled plan against whatever functions you point it at.

    The functions come from `--runtime module:name`, where `name` is either a
    `Runtime` or a plain dict of candidate id to callable. Keeping them outside
    the graph is the whole design — a dry run and a live run are the same plan
    with a different runtime, not two code paths.
    """
    import importlib
    import pathlib

    from browsergraph import execute as _execute
    from browsergraph.compile import CompileError, compile_route
    from browsergraph.demo import workbench as demo_workbench
    from browsergraph.workbench import WorkbenchDefinition

    bench = (WorkbenchDefinition.load(args.config) if args.config
             else demo_workbench())

    store = None
    if args.evidence:
        from browsergraph.evidence import Evidence
        path = pathlib.Path(args.evidence)
        store = (Evidence.load(str(path)) if path.exists() else Evidence())

    solutions = {s.id: s for s in bench.solutions}
    if args.budget:
        from browsergraph import search
        if not bench.optimization_profiles:
            print("--budget needs the workbench to carry an optimization profile")
            return 1
        found = search.within(bench, bench.optimization_profiles[0],
                              evaluations=args.budget, evidence=store)
        route = found.route
        print(f"searched within {args.budget:,} evaluations: {found.strategy}")
        for note in found.notes[-1:]:
            print("  " + note)
        print()
    elif args.route and args.route in solutions:
        route = solutions[args.route].route
    elif args.route:
        print(f"unknown route {args.route!r}; known: {', '.join(solutions) or 'none'}")
        return 1
    else:
        route = {s.id: s.candidates[0] for s in bench.leaf_stages if s.candidates}

    try:
        plan = compile_route(bench, route, source=args.route or "first")
    except CompileError as problem:
        print("cannot compile:")
        for line in problem.problems:
            print("  " + line)
        return 1

    runtime = _execute.Runtime()
    if args.runtime:
        module_name, _, attribute = args.runtime.partition(":")
        module = importlib.import_module(module_name)
        found = getattr(module, attribute or "RUNTIME")
        runtime = found if isinstance(found, _execute.Runtime) else _execute.Runtime(found)

    missing = runtime.missing(plan)
    if missing:
        print(f"{len(missing)} step(s) have no function behind them:")
        for candidate in missing:
            print("  " + candidate)
        print("\nPoint --runtime at a module holding them, e.g. "
              "--runtime mypkg.nodes:RUNTIME")
        return 1

    result = _execute.run(plan, runtime, workspace=args.workspace,
                          workers=args.workers,
                          allow_effects=not args.dry_run,
                          strict=not args.keep_going)
    print(result.text())
    receipt = result.receipt(task=args.route or "")
    if args.receipt:
        print(f"\nreceipt: {receipt.write(args.receipt)}")
    if store is not None:
        # The loop, in one command: what just ran becomes what is known, so the
        # next call starts from it rather than from the numbers in the file.
        store.from_receipt(receipt)
        store.save(args.evidence)
        print(f"evidence updated: {args.evidence}")
    return 0 if result.ok else 1


def cmd_compile(args) -> int:
    """Resolve a route into an immutable, content-addressed plan."""
    from browsergraph.compile import CompileError, compile_route
    from browsergraph.demo import workbench as demo_workbench
    from browsergraph.workbench import WorkbenchDefinition

    bench = (WorkbenchDefinition.load(args.config) if args.config
             else demo_workbench())
    solutions = {s.id: s for s in bench.solutions}
    if args.route not in solutions:
        print(f"unknown route {args.route!r}; known: {', '.join(solutions)}")
        return 1
    try:
        plan = compile_route(bench, solutions[args.route].route,
                             source=args.route)
    except CompileError as e:
        print("cannot compile:")
        for problem in e.problems:
            print("  " + problem)
        return 1
    if args.json:
        print(plan.to_json())
    else:
        print(plan.text())
    if args.against and args.against in solutions:
        from browsergraph.compile import diff
        other = compile_route(bench, solutions[args.against].route)
        print(f"\nversus {args.against}:")
        for line in diff(other, plan):
            print("  " + line)
    return 0


def cmd_evidence(args) -> int:
    """What has been learned, and how much of the choice is still open."""
    from browsergraph.demo import workbench as demo_workbench
    from browsergraph.evidence import Evidence, context_chain, stages_of
    from browsergraph.policy import Policy
    from browsergraph.workbench import WorkbenchDefinition

    bench = (WorkbenchDefinition.load(args.config) if args.config
             else demo_workbench())
    store = Evidence.load(args.store)
    stages = stages_of(bench, Policy.permissive())
    context = context_chain(*(args.context or []))
    if args.suggest:
        route = store.suggest(stages, context)
        names = bench.candidates_by_id
        for stage in bench.leaf_stages:
            chosen = route.get(stage.id, "")
            posterior = store.posterior(chosen, context)
            print(f"  {stage.name:<24} "
                  f"{names[chosen].name if chosen in names else chosen:<40} "
                  f"p={posterior.rate:.2f} n={posterior.runs}")
        return 0
    print(store.report(stages, context))
    return 0


def cmd_check(args) -> int:
    """Is this a valid graph solution? Fast, local, and specific about the fix.

    The command an LLM harness runs after every edit. Documentation does not
    stop a model inventing a field; a check that answers in under a second and
    names the fix does — and it works the same for a person.
    """
    import time

    from browsergraph.workbench import WorkbenchDefinition

    started = time.monotonic()
    findings: list[dict] = []
    bench = None
    try:
        if args.config:
            bench = WorkbenchDefinition.load(args.config)
        else:
            from browsergraph.demo import workbench as demo_workbench
            bench = demo_workbench()
    except Exception as e:
        findings.append({"severity": "error", "where": args.config or "demo",
                         "message": f"{type(e).__name__}: {e}",
                         "fix": "the file must be JSON matching "
                                "browsergraph/schemas/workbench.schema.json"})

    summary: dict[str, Any] = {}
    if bench is not None:
        for problem in bench.validate():
            findings.append({"severity": "error", "where": "workbench",
                             "message": problem, "fix": ""})
        leaves = bench.leaf_stages
        summary = {"stages": len(bench.stages), "sub_steps": len(leaves),
                   "definitions": len(bench.nodes),
                   "candidates": len(bench.candidates),
                   "routes": bench.route_count(),
                   "computations": bench.computation_count(),
                   "solutions": len(bench.solutions)}
        # Advice, not errors: a sub-step with one candidate is a hard-coded
        # choice wearing a graph's clothes, and a stage nobody can route
        # through is a design mistake the type checker cannot see.
        for leaf in leaves:
            if len(leaf.candidates) == 1:
                findings.append({
                    "severity": "advice", "where": leaf.id,
                    "message": f"only one candidate admitted to {leaf.id!r}",
                    "fix": "either add an alternative or fold this into its "
                           "neighbour — a sub-step with one option is a "
                           "hard-coded choice, not a decision"})
        if not bench.solutions:
            findings.append({"severity": "advice", "where": "solutions",
                             "message": "no named route",
                             "fix": "declare at least one, so there is "
                                    "something to compare against"})

    seconds = time.monotonic() - started
    errors = [f for f in findings if f["severity"] == "error"]
    if args.json:
        print(json.dumps({"ok": not errors, "seconds": round(seconds, 3),
                          "summary": summary, "findings": findings}, indent=2))
        return 1 if errors else 0

    for finding in findings:
        mark = "ERROR" if finding["severity"] == "error" else "advice"
        print(f"[{mark}] {finding['where']}: {finding['message']}")
        if finding["fix"]:
            print(f"         fix: {finding['fix']}")
    if summary:
        print(f"\n{summary['stages']} stages / {summary['sub_steps']} sub-steps · "
              f"{summary['candidates']} candidates · "
              f"{summary['routes']:,} routes")
    print(("FAILED — " + str(len(errors)) + " error(s)") if errors
          else f"valid ({seconds:.2f}s)")
    return 1 if errors else 0


def cmd_capabilities(args) -> int:
    """Which engines can do what, and which could run a given graph."""
    from browsergraph import capabilities as caps
    nodes: tuple = ()
    if args.config:
        from browsergraph.config import load_graph
        graph, _ = load_graph(args.config)
        nodes = tuple(graph.nodes.values())
    print(caps.report(nodes))
    if nodes:
        for engine in Engine:
            gaps = caps.missing(engine, nodes)
            if gaps and args.verbose:
                print(f"\n  {engine.value} cannot run:")
                for gap in gaps:
                    print(f"      {gap}")
    return 0


def cmd_models(args) -> int:
    """Which model this host would use for each job, and why."""
    from browsergraph.router import Router, describe_roles

    if args.roles:
        print(describe_roles())
        return 0
    router = Router.load(args.host, os.environ.get("OLLAMA_API_KEY", ""))
    print(router.report())
    unfilled = router.unfilled()
    if unfilled:
        print(f"\n{len(unfilled)} role(s) unfilled — pull a model, or accept "
              f"that those jobs cannot run here.")
    return 1 if unfilled and args.strict else 0


def cmd_route(args) -> int:
    """Propose a complete route under a policy and an objective profile."""
    from browsergraph import search
    from browsergraph.demo import workbench as demo_workbench
    from browsergraph.policy import Policy, review
    from browsergraph.workbench import WorkbenchDefinition

    bench = (WorkbenchDefinition.load(args.config) if args.config
             else demo_workbench())
    profiles = {p.id: p for p in bench.optimization_profiles}
    if not profiles:
        print("this workbench declares no optimization profiles")
        return 1
    profile = profiles.get(args.profile) or next(iter(profiles.values()))

    granted = set(args.allow or []) or set(Policy.permissive().permissions)
    policy = Policy(permissions=frozenset(granted),
                    allow_external_effects=not args.no_effects,
                    deterministic_only=args.deterministic,
                    max_cost_usd=args.max_cost, max_latency_ms=args.max_latency,
                    name=args.policy_name)

    if args.gates:
        print(review(bench, policy).text())
        return 0

    if args.compare:
        for name, proposal in search.compare_strategies(
                bench, profile, policy=policy).items():
            print(f"--- {name} ---")
            print(proposal.text(bench))
            print()
        return 0

    proposal = search.propose(bench, profile, policy=policy,
                              strategy=args.strategy, beam=args.beam)
    if args.json:
        print(json.dumps(proposal.to_dict(), indent=2))
        return 0 if proposal.ok else 1
    print(proposal.text(bench))
    return 0 if proposal.ok else 1


def cmd_fetch(args) -> int:
    """Download a browser or driver into the user cache."""
    from browsergraph import fetch as f

    # `--match` before the listing branch: `fetch --match system_chrome` names no
    # positional, so checking `what` first silently printed the catalogue and
    # exited 0 — a command that looked like it worked and fetched nothing.
    if args.match:
        got = f.matching_chromedriver(args.match)
        print(f"chromedriver for {args.match}: "
              + (f"{got.version}\n  {got.path}" if got.ok else f"unavailable — {got.error}"))
        return 0 if got.ok else 1

    if args.what in (None, "", "list"):
        print("fetchable:")
        print(f.report(offline=not args.remote))
        return 0

    kw = {}
    if args.milestone:
        kw["milestone"] = args.milestone
    if args.version:
        kw["version"] = args.version

    if args.dry_run:
        plan = f.plan(args.what, **kw)
        print(f"{plan.what}: {plan.version or '?'} from {plan.source}\n  "
              + (plan.url if plan.ok else f"unavailable — {plan.error}"))
        return 0 if plan.ok else 1

    got = f.fetch(args.what, force=args.force, **kw)
    if not got.ok:
        print(f"could not fetch {args.what}: {got.error}")
        return 1
    print(f"{got.what} {got.version} "
          + ("(already cached)" if got.cached else f"from {got.source}") + f"\n  {got.path}")
    return 0


def cmd_space(args) -> int:
    """Draw the dimension space: one plane per axis, one line per runnable spec."""
    from browsergraph.spacemap import explore, to_html, to_text
    space = explore(limit=args.limit)
    if args.html:
        from pathlib import Path
        Path(args.html).write_text(
            "<!doctype html><meta charset=utf-8>"
            "<body style='margin:0;padding:18px;background:#f6f8fa'>" + to_html(space),
            encoding="utf-8")
        print(f"wrote {args.html}  ({space.summary()})")
        return 0
    print(to_text(space))
    return 0


def cmd_planes(args) -> int:
    """Draw the task as planes of interchangeable candidates."""
    from browsergraph.planmap import (
        observe,
        planes,
        routes,
        score_routes,
        to_html,
        to_text,
    )
    ps = planes()
    naive = score_routes(ps, routes(ps, limit=100000))[0]
    learned = None
    if args.demo:
        # An illustrative history, clearly labelled as such: what a defended,
        # JavaScript-rendered site looks like after a few dozen runs.
        observe(ps, {"http": (1, 20), "playwright": (6, 20), "patchright": (18, 20),
                     "css": (4, 20), "healing": (15, 18), "llm_selector": (9, 10),
                     "wait_for": (19, 20), "dwell": (6, 20), "click": (18, 20),
                     "screenshot": (20, 20), "extract": (17, 18)})
        learned = score_routes(ps, routes(ps, limit=100000))[0]
    if args.html:
        from pathlib import Path
        Path(args.html).write_text(
            "<!doctype html><meta charset=utf-8>"
            "<body style='margin:0;padding:18px;background:#f6f8fa'>"
            + to_html(ps, before=naive, after=learned), encoding="utf-8")
        print(f"wrote {args.html}")
        return 0
    print(to_text(ps, learned or naive))
    return 0


def cmd_nodes(args) -> int:
    """The contract table — what every node kind promises."""
    from browsergraph.nodes import REGISTRY

    if args.json:
        # The same nodes as portable manifests, so a registry, a planner or
        # another language can read them without importing this package.
        import json as _json

        from browsergraph.manifest import registry_manifests
        print(_json.dumps([m.to_dict() for m in registry_manifests()], indent=2))
        return 0

    from browsergraph.contracts import describe_all
    print(f"{len(REGISTRY)} node kinds\n")
    print(describe_all(REGISTRY.values()))
    return 0


def cmd_graph(args) -> int:
    """Draw a graph and check its contracts fit together."""
    from browsergraph.config import load_graph
    graph, _spec = load_graph(args.config)
    if args.mermaid:
        print(graph.to_mermaid())
        return 0
    if args.json:
        print(json.dumps(graph.to_dict(), indent=2, default=str))
        return 0
    print(f"{graph.name}: {len(graph.nodes)} nodes, {len(graph.edges)} edges")
    for lvl_no, level in enumerate(graph.levels(), 1):
        print(f"  level {lvl_no}: {', '.join(level)}")
    print("\ncontracts:")
    for c in graph.contracts():
        print(f"  {c.describe()}")
    result = graph.audit()
    print(f"\naudit: {result.text()}")
    return 0 if result.ok else 1


def _load_bench(config):
    """A workbench from a path, or the demonstration one. Used by several."""
    from browsergraph.demo import workbench as demo_workbench
    from browsergraph.workbench import WorkbenchDefinition

    return WorkbenchDefinition.load(config) if config else demo_workbench()


def _load_attribute(reference: str, default: str = ""):
    """`module:name`, the same convention `--runtime` already uses."""
    import importlib

    module_name, _, attribute = reference.partition(":")
    return getattr(importlib.import_module(module_name), attribute or default)


def cmd_draw(args) -> int:
    """Draw a workbench: a self-contained page, or Mermaid, or JSON.

    `viz` could draw any of this from the first day it existed and none of it
    was reachable without writing Python. A library whose central argument is
    "look at the shape before you believe it is that shape" should not require
    a script to look.

    The page is self-contained — no CDN, no fonts, no fetch — so it opens from
    a `file://` URL on a machine with no network.
    """
    from browsergraph import viz

    bench = _load_bench(args.config)
    route = {}
    if args.route:
        named = {s.id: s for s in bench.solutions}
        if args.route not in named:
            print(f"unknown route {args.route!r}; known: "
                  f"{', '.join(named) or 'none'}")
            return 1
        route = dict(named[args.route].route)

    if args.format == "mermaid":
        print(viz.to_mermaid(bench, route or None))
        return 0
    if args.format == "json":
        print(viz.to_json(bench, route or None))
        return 0

    funnel = [("every route", bench.route_count())]
    if bench.computation_count() != bench.route_count():
        funnel.append(("distinguishable behaviours", bench.computation_count()))
    written = viz.write_report(bench, args.out, route=route or None,
                               search=funnel)
    print(f"wrote {written}")
    print(f"  {len(bench.leaf_stages)} sub-steps, "
          f"{bench.route_count():,} routes"
          + (f", {bench.computation_count():,} distinguishable behaviours"
             if bench.computation_count() != bench.route_count() else ""))
    return 0


def cmd_solve(args) -> int:
    """Try routes, run them, judge the *output*, keep the best and a fallback.

    The library's shortest path from a graph to an answer, and it was reachable
    only from Python. Everything it needs is already a command-line convention
    here: `--runtime module:name` for the functions, `--verify module:name` for
    the judge.

    Without `--verify`, "did it work" means "did it not raise", which a route
    returning nothing passes with full marks. That is exactly the failure this
    whole design is arranged against, so it is refused rather than defaulted —
    unless you say `--accept-anything`, which prints what it is agreeing to.
    """
    from browsergraph import solve as _solve
    from browsergraph.execute import Runtime

    bench = _load_bench(args.config)
    if not bench.optimization_profiles:
        print("this workbench carries no optimization profile, so there is "
              "nothing to rank routes by. Add one, or use `execute`.")
        return 1

    runtime = Runtime()
    if args.runtime:
        found = _load_attribute(args.runtime, "RUNTIME")
        runtime = found if isinstance(found, Runtime) else Runtime(found)

    if args.verify:
        verify = _load_attribute(args.verify, "verify")
    elif args.stage:
        verify = _solve.outputs_are_not_empty(*args.stage)
    elif args.accept_anything:
        print("no verifier: a route counts as working if nothing raised. A "
              "route that returns an empty result will score full marks.\n")
        verify = None
    else:
        print("solve needs to know what a good answer looks like. Pick one:\n"
              "  --stage NAME          accept when that stage produced "
              "something, and score on how much\n"
              "  --verify module:name  your own judge, given the finished run\n"
              "  --accept-anything     score on 'nothing raised', which cannot "
              "tell empty from correct")
        return 1

    store = None
    if args.evidence:
        import pathlib

        from browsergraph.evidence import Evidence
        path = pathlib.Path(args.evidence)
        store = Evidence.load(str(path)) if path.exists() else Evidence()

    answer = _solve.solve(bench, runtime, verify=verify, attempts=args.attempts,
                          budget=args.budget, workspace=args.workspace,
                          workers=args.workers, evidence=store)
    print(answer.text(bench))

    if store is not None and args.evidence:
        store.save(args.evidence)
        print(f"\n  evidence updated: {args.evidence}")
    if args.out:
        from browsergraph import viz
        written = viz.write_report(
            bench, args.out, route=answer.champion or None,
            alternative=answer.fallbacks[0] if answer.fallbacks else None,
            run=answer.attempts[-1].run if answer.attempts else None,
            solution=answer)
        print(f"  report: {written}")
    return 0 if answer.ok else 1


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

    bs = sub.add_parser("bootstrap", help="install until a browser launches")
    bs.add_argument("--engine", default="playwright")
    bs.add_argument("--no-install", action="store_true", help="do not pip/download anything")
    bs.add_argument("--no-apt", action="store_true", help="do not install system libraries")
    bs.set_defaults(fn=cmd_bootstrap)

    cm = sub.add_parser("compile", help="resolve a route into a hashed plan")
    cm.add_argument("route", nargs="?", default="cheapest")
    cm.add_argument("config", nargs="?", help="a workbench JSON file")
    cm.add_argument("--against", help="diff against another named route")
    cm.add_argument("--json", action="store_true")
    cm.set_defaults(fn=cmd_compile)

    ex = sub.add_parser("execute", help="run a compiled plan against real functions")
    ex.add_argument("route", nargs="?", help="a named solution (default: first candidates)")
    ex.add_argument("config", nargs="?", help="a workbench JSON file")
    ex.add_argument("--runtime", help="module:name holding the functions")
    ex.add_argument("--workspace", help="folder for artifacts")
    ex.add_argument("--workers", type=int, default=1,
                    help="run independent steps at once")
    ex.add_argument("--dry-run", action="store_true",
                    help="refuse any step that declares an effect")
    ex.add_argument("--keep-going", action="store_true",
                    help="do not stop at the first failure")
    ex.add_argument("--budget", type=int,
                    help="search for the best route within this many evaluations")
    ex.add_argument("--evidence", help="an evidence JSON file to learn from and update")
    ex.add_argument("--receipt", help="write a run receipt to this path")
    ex.set_defaults(fn=cmd_execute)

    vf = sub.add_parser("verify", help="run every route and check the controls")
    vf.add_argument("config", nargs="?", help="a workbench JSON file")
    vf.add_argument("--runtime", help="module:name holding the functions")
    vf.add_argument("--per-stage", type=int, default=2,
                    help="candidates per step to combine (default 2)")
    vf.add_argument("--limit", type=int, default=200,
                    help="refuse to verify more routes than this")
    vf.add_argument("--expect-failure", action="append",
                    help="a candidate whose routes must fail (repeatable)")
    vf.set_defaults(fn=cmd_verify)

    ev = sub.add_parser("evidence", help="what has been learned, in bits")
    ev.add_argument("config", nargs="?", help="a workbench JSON file")
    ev.add_argument("--store", default="evidence.json")
    ev.add_argument("--context", action="append",
                    help="context key, most specific first (repeatable)")
    ev.add_argument("--suggest", action="store_true",
                    help="propose a route by sampling the posteriors")
    ev.set_defaults(fn=cmd_evidence)

    ck = sub.add_parser("check", help="validate a graph solution, fast")
    ck.add_argument("config", nargs="?", help="a workbench JSON file (default: the demo)")
    ck.add_argument("--json", action="store_true", help="machine-readable findings")
    ck.set_defaults(fn=cmd_check)

    cp = sub.add_parser("capabilities", help="what each engine can do")
    cp.add_argument("config", nargs="?", help="a graph config, to check against")
    cp.add_argument("-v", "--verbose", action="store_true")
    cp.set_defaults(fn=cmd_capabilities)

    md = sub.add_parser("models", help="which model for which job, and why")
    md.add_argument("--host", default="", help="model host (default: OLLAMA_HOST)")
    md.add_argument("--roles", action="store_true", help="describe the roles only")
    md.add_argument("--strict", action="store_true", help="exit 1 if a role is unfilled")
    md.set_defaults(fn=cmd_models)

    rt = sub.add_parser("route", help="propose a route under a policy and profile")
    rt.add_argument("config", nargs="?", help="a workbench JSON file (default: the demo)")
    rt.add_argument("--profile", default="profile.balanced")
    rt.add_argument("--strategy", default="auto",
                    choices=("auto", "exhaustive", "beam", "greedy"))
    rt.add_argument("--beam", type=int, default=8)
    rt.add_argument("--allow", action="append",
                    help="grant a permission (repeatable); default grants all")
    rt.add_argument("--no-effects", action="store_true",
                    help="forbid candidates that change external state")
    rt.add_argument("--deterministic", action="store_true",
                    help="only deterministic candidates")
    rt.add_argument("--max-cost", type=float, help="per-candidate cost ceiling")
    rt.add_argument("--max-latency", type=float, help="per-candidate latency ceiling")
    rt.add_argument("--policy-name", default="cli")
    rt.add_argument("--gates", action="store_true",
                    help="show what the policy blocks, and why")
    rt.add_argument("--compare", action="store_true",
                    help="run greedy, beam and exhaustive side by side")
    rt.add_argument("--json", action="store_true")
    rt.set_defaults(fn=cmd_route)

    ft = sub.add_parser("fetch", help="download a browser or driver into the cache")
    ft.add_argument("what", nargs="?", help="chrome, chromedriver, geckodriver, ... "
                                            "(omit to list)")
    ft.add_argument("--milestone", help="Chrome major version to match, e.g. 150")
    ft.add_argument("--match", help="fetch a chromedriver matching this binary, "
                                    "e.g. system_chrome")
    ft.add_argument("--version", help="an exact version instead of the newest")
    ft.add_argument("--dry-run", action="store_true", help="print the URL, download nothing")
    ft.add_argument("--force", action="store_true", help="re-download even if cached")
    ft.add_argument("--remote", action="store_true",
                    help="when listing, look up current versions online")
    ft.set_defaults(fn=cmd_fetch)

    sp = sub.add_parser("space", help="every dimension and every runnable path")
    sp.add_argument("--html", help="write an interactive diagram to this file")
    sp.add_argument("--limit", type=int, default=1500, help="paths to draw")
    sp.set_defaults(fn=cmd_space)

    pl = sub.add_parser("planes", help="task planes and candidate routes")
    pl.add_argument("--html", help="write an interactive diagram to this file")
    pl.add_argument("--demo", action="store_true",
                    help="overlay an illustrative learned route")
    pl.set_defaults(fn=cmd_planes)

    nd = sub.add_parser("nodes", help="node kinds and their contracts")
    nd.add_argument("--json", action="store_true", help="emit portable manifests")
    nd.set_defaults(fn=cmd_nodes)

    wbp = sub.add_parser("workbench",
                         help="stages, candidates and routes as a standalone studio")
    wbp.add_argument("config", nargs="?", help="a workbench JSON file (default: the demo)")
    wbp.add_argument("-o", "--out", default="browsergraph-workbench.html")
    wbp.add_argument("--view", default="candidates",
                     choices=("candidates", "network", "compare", "builder", "feedback"),
                     help="which projection opens first")
    wbp.add_argument("--export-data", help="also write the normalized JSON here")
    wbp.add_argument("--suite", help="write one file per projection into this directory")
    wbp.set_defaults(fn=cmd_workbench)

    dw = sub.add_parser("draw", help="a picture of a workbench, as one HTML file")
    dw.add_argument("config", nargs="?", help="a workbench JSON file (default: the demo)")
    dw.add_argument("-o", "--out", default="workbench.html")
    dw.add_argument("--route", help="a named solution to highlight")
    dw.add_argument("--format", default="html", choices=("html", "mermaid", "json"),
                    help="html writes a self-contained page; the others print")
    dw.set_defaults(fn=cmd_draw)

    sv = sub.add_parser("solve", help="try routes, judge the output, keep the best")
    sv.add_argument("config", nargs="?", help="a workbench JSON file (default: the demo)")
    sv.add_argument("--runtime", help="module:name holding the functions")
    sv.add_argument("--verify", help="module:name of a judge, given the finished run")
    sv.add_argument("--stage", action="append",
                    help="accept when this stage produced something (repeatable)")
    sv.add_argument("--accept-anything", action="store_true",
                    help="score on 'nothing raised' — cannot tell empty from correct")
    sv.add_argument("--attempts", type=int, default=8, help="routes to actually run")
    sv.add_argument("--budget", type=int, default=60,
                    help="routes to score while choosing each one")
    sv.add_argument("--workers", type=int, default=1)
    sv.add_argument("--workspace", help="folder for artifacts")
    sv.add_argument("--evidence", help="an evidence JSON file to learn from and update")
    sv.add_argument("-o", "--out", help="write a report page here")
    sv.set_defaults(fn=cmd_solve)

    gr = sub.add_parser("graph", help="draw a graph and audit its contracts")
    gr.add_argument("config")
    gr.add_argument("--mermaid", action="store_true", help="emit a Mermaid diagram")
    gr.add_argument("--json", action="store_true", help="emit the structure as JSON")
    gr.set_defaults(fn=cmd_graph)

    v = sub.add_parser("serve", help="HTTP API")
    v.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8800)))
    v.set_defaults(fn=cmd_serve)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
