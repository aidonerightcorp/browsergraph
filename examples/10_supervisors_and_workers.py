#!/usr/bin/env python3
"""Several models doing one job, and the answer that looks complete.

The brief asks for four fields. The document contains three of them. That is the
ordinary situation, and what a multi-agent pipeline does about it is the whole
question — because the most confident-looking output in this script is the only
wrong one.

    python examples/10_supervisors_and_workers.py
"""
from browsergraph import execute, packs
from browsergraph.compile import compile_route
from browsergraph.packs import agents

pack = packs.get("agents")
bench = pack.workbench()


def build(**route):
    full = {"brief": "brief.four_fields", "plan": "plan.disjoint", **route}
    return execute.run(compile_route(bench, full), pack.runtime())


print("The source document, in full:\n")
for section, text in agents.SOURCE.items():
    print(f"  [{section}] {text}")
print("\nThe brief asks for: tenant, rent, notice_period, insurer.")
print("There is no insurer named anywhere above.\n")

print("=" * 74)
print(f"{'workers':<16}{'critic':<17}{'synthesise':<21}{'fields':>7}{'invented':>9}")
for work in ("work.grounded", "work.confident", "work.flaky"):
    for critic, synth in (("critic.grounded", "synthesise.filtered"),
                          ("critic.none", "synthesise.all")):
        run = build(work=work, critic=critic, synthesise=synth,
                    verify="verify.covers_brief")
        answer, verdict = run.output("synthesise"), run.output("verify")
        print(f"{work:<16}{critic:<17}{synth:<21}"
              f"{len(answer['fields']):>7}{verdict['ungrounded']:>9}")

print("\nThe row with four fields is the one with a fabrication in it.\n")

# --- the two verifiers, on the same run -------------------------------------

print("=" * 74)
print("The same worker output, checked two ways\n")
for check in ("verify.nonempty", "verify.covers_brief"):
    run = build(work="work.confident", critic="critic.none",
                synthesise="synthesise.all", verify=check)
    verdict = run.output("verify")
    print(f"  {check:<22}{'PASS' if verdict['ok'] else 'FAIL'}   {verdict['why']}")

print("\n  The first is what most pipelines check. It passes an answer whose\n"
      "  insurer field was invented by a worker being helpful.\n")

# --- what the critic is for -------------------------------------------------

print("=" * 74)
print("What a critic that reads the results against the tasks finds\n")
run = build(work="work.confident", critic="critic.grounded",
            synthesise="synthesise.filtered", verify="verify.covers_brief")
findings = run.output("critic")
for rejected in findings["rejected"]:
    print(f"  rejected {rejected['field']}={rejected['value']!r}")
    print(f"    {rejected['why']}")

answer = run.output("synthesise")
print(f"\n  final answer:  {answer['fields']}")
print(f"  declared gaps: {answer['gaps']}")
print(f"  verdict:       {'PASS' if run.output('verify')['ok'] else 'FAIL'}")
print("\n  Three fields and a stated gap is a correct answer to this brief.\n"
      "  Four fields is not.\n")

# --- the quiet failure ------------------------------------------------------

print("=" * 74)
print("The quiet one: a worker that returned an apology\n")
run = build(work="work.flaky", critic="critic.grounded",
            synthesise="synthesise.filtered", verify="verify.covers_brief")
verdict = run.output("verify")
print(f"  fields returned: {sorted(run.output('synthesise')['fields'])}")
print(f"  verdict:         {'PASS' if verdict['ok'] else 'FAIL'}")
print(f"  why:             {verdict['why']}")
print("\n  Everything downstream of the refusal behaved correctly. That is why\n"
      "  the loss is invisible without a check that counts against the brief.")

# --- and what the pack's own verifier makes of all of it --------------------

print("\n" + "=" * 74)
print("Scored by the pack's verifier, which charges more for an invented\n"
      "field than for a missing one\n")
for work in ("work.grounded", "work.confident", "work.flaky"):
    for critic, synth in (("critic.grounded", "synthesise.filtered"),
                          ("critic.none", "synthesise.all")):
        run = build(work=work, critic=critic, synthesise=synth,
                    verify="verify.covers_brief")
        ok, score = agents.answers_without_inventing(run)
        print(f"  {work:<15}{critic:<17}{synth:<21}"
              f"{'ok ' if ok else 'no '} {score:.2f}")
