#!/usr/bin/env python3
"""Start from the shape somebody already worked out.

The claim in `taxonomy.py` is that engineering pipelines come in about forty
shapes. This script is what that claim is *for*: given a job in front of you,
find the category, get a skeleton with typed ports and the mistakes people make
in it, and fill the slots.

    python examples/13_find_your_shape.py
"""
from browsergraph import packs, taxonomy, templates
from browsergraph.viz import to_mermaid

# --- 1. is my problem one of the known shapes? ------------------------------

print("Say the job is: 'attach the county and check the address is real'.\n")
for category in taxonomy.search("address"):
    print(f"  {category.id:<22}{category.title}")
    print(f"  {'':<22}{category.question}")

found = taxonomy.get("enrich.geo")
print(f"\n  shape:    {found.shape}")
print(f"  fails as: {found.fails_as}")
print(f"  template: {found.template}")
print(f"  pack:     {found.pack}")

# --- 2. what does the shape look like? --------------------------------------

print("\n" + "=" * 74)
print("The skeleton — typed ports, no candidates, nothing chosen yet\n")

template = templates.get(found.template)
skeleton = template.skeleton()
print(to_mermaid(skeleton))

print("\n  and the mistakes people make in this shape, which travel with it:")
for anti in template.anti_patterns:
    print(f"    - {anti}")

# --- 3. fill it in ----------------------------------------------------------

print("\n" + "=" * 74)
print("Filling the slots is one call, and the compiler checks it\n")

filled = template.instantiate({
    "load": ["my.reader"],
    "parse": ["my.parser", "my.other_parser"],
    "lookup": ["my.lookup"],
    "reconcile": ["my.reconciler"],
    "validate": ["my.validator"],
    "attach": ["my.attacher"],
})
print(f"  {len(filled.leaf_stages)} steps, {filled.route_count()} routes, "
      f"{len(filled.validate())} structural problems")

try:
    template.instantiate({"lookuo": ["typo.here"]})
except KeyError as problem:
    print(f"\n  and a typo in a slot name is refused rather than silently\n"
          f"  dropping a step:\n    {str(problem)[:110]}...")

# --- 4. or take the one with the code already written -----------------------

print("\n" + "=" * 74)
print("Or start from the pack, which is the same shape with functions\n")

pack = packs.get(found.pack)
bench = pack.workbench()
print(f"  {pack.name}: {pack.summary}")
for stage in bench.leaf_stages:
    print(f"    {stage.id:<12}{', '.join(stage.candidates)}")
print(f"\n  {bench.route_count()} routes, every one of them executed by a test.")

# --- 5. and what the map says about the rest --------------------------------

print("\n" + "=" * 74)
print("Coverage, counted rather than claimed\n")
coverage = taxonomy.coverage()
print(f"  {coverage.total} categories in {len(taxonomy.FAMILIES)} families")
print(f"  {coverage.expressible} have a checkable shape "
      f"({coverage.expressible_fraction:.0%})")
print(f"  {coverage.runnable} have code that runs "
      f"({coverage.runnable_fraction:.0%})")
print(f"\n  {len(coverage.gaps)} gaps, which is the useful part — the first few:")
for category_id, missing in coverage.gaps[:5]:
    print(f"    {category_id:<24}{missing}")

print("\n  And what the map deliberately does not cover:")
for name, _ in taxonomy.OUT_OF_SCOPE:
    print(f"    {name}")

print("\n  browsergraph taxonomy --coverage      the whole thing, in a terminal")
