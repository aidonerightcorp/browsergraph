# Coding-agent and LLM harness integration

The repository intentionally uses progressive disclosure instead of copying one
large prompt into every vendor-specific file.

| File | Purpose | Loading behavior |
|---|---|---|
| `AGENTS.md` | Canonical repository rules and compact architecture guardrails | Codex, Copilot agents, Cursor, and Windsurf read it directly |
| `CLAUDE.md` | Claude Code adapter | Imports `AGENTS.md` using Claude's supported `@` syntax |
| `GEMINI.md` | Gemini CLI adapter | Imports `AGENTS.md` using Gemini's supported `@` syntax |
| `.github/copilot-instructions.md` | Broad Copilot IDE compatibility | Points at the canonical rules and specification |
| `.agents/skills/model-solution-graph/SKILL.md` | Detailed on-demand modeling procedure | Loaded only when a matching task activates the workspace skill |
| `UNIVERSAL_NODE_GRAPH_SPEC.md` | Normative architecture | Read for design or implementation work |
| `RESEARCH_FOUNDATIONS.md` | Primary-source rationale | Read when evaluating or changing a tradeoff |
| `llms.txt` | Machine-friendly documentation map | Entry point for crawlers and unfamiliar harnesses |

## Why not duplicate the full prompt?

Duplicated prompt files drift, consume context, and create precedence conflicts.
The thin-adapter pattern gives each harness its supported discovery filename
while preserving one reviewable source of behavioral instructions.

Instruction files guide agent behavior; they are not an enforcement boundary.
The Python compiler, JSON Schemas, diagnostics, tests, content digests, and CI
enforce architecture independently of whether a model followed a prompt.

## Recommended harness workflow

1. Load the root instruction file.
2. Activate `model-solution-graph` for a new domain or architectural task.
3. Compile the semantic program before implementing runtime adapters.
4. Run universal conformance tests before domain-specific integration tests.
5. Include compiler diagnostics, search reports, frozen plan digests, and
   receipts in the review output; do not rely on prose assurance.

