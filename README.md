# Project OS / Evolution Agent

An auditable, local-first agent loop for improving a personal website without letting an LLM approve its own changes.

```text
Observe → Evaluate → Diagnose → Improve → Verify → Commit / Reject
```

Project OS is an early V0.2 reference implementation. It makes every run traceable in SQLite and accepts a candidate only when an independent evaluator reports a strict improvement without regressing protected dimensions.

## What works today

- A LangGraph workflow with a deliberately small runtime `AgentState`.
- SQLite records for projects, observations, quality reports, tasks, versions, claims, evidence, experiments, failures, and decisions.
- A repeatable HTML demo with deterministic repairs.
- A profile adapter for an Astro fansite, with source-level quality checks across correctness, evidence coverage, usability, visual structure, originality, performance, and consistency.
- Isolated Git-worktree validation: build a candidate before applying a source change.
- A bounded web-research pipeline. It honours `robots.txt`, stores evidence excerpts, and requires two independent trusted sources before it can publish a generated news draft.
- A policy gate with explicit protected dimensions and validation requirements; approval is not a model judgment.
- Seven independent, read-only quality agents—correctness, evidence, usability, visual structure, originality, performance and consistency—plus a Meta Evaluator that only aggregates their results.
- A workspace-scoped, optimistic-concurrency change-set applier for future multi-file coding agents.
- An isolated multi-file worktree verifier that applies a whole candidate change set before running configured build or test commands.

## Intentional limitations

This is not yet an autonomous browser-coding system. The current visual score is based on source heuristics, not rendered screenshots; evidence coverage is not a substitute for verifying every claim; and the improvers are narrow deterministic transformations. These limits are safeguards, not hidden capabilities.

## Quick start

Requires Python 3.11+ and Git for worktree verification.

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
python -m unittest discover -s tests -t . -v
```

Run the safe demo:

```bash
project-os init-demo
project-os run --site demo_site/index.html
project-os history
```

`init-demo` creates a deliberately incomplete page. The deterministic evaluator identifies one repair per run. After the known issues are resolved, a subsequent run is rejected instead of making an ungrounded edit.

## Applying it to your site

Start with a dry run, which writes an audit record but never edits the target:

```bash
project-os run --profile shan-yichun-splash --dry-run \
  --site /path/to/site/src/layouts/BaseLayout.astro \
  --db .agent-data/site.db
```

For a candidate that must compile in an isolated Git worktree before it is applied:

```bash
project-os run --profile shan-yichun-splash --verify-worktree \
  --site /path/to/site/src/layouts/BaseLayout.astro \
  --db .agent-data/site.db
```

Use a disposable multi-cycle preview to examine a profile without changing the real site:

```bash
project-os evolve-fansite-preview \
  --site /path/to/site/src/layouts/BaseLayout.astro \
  --db .agent-data/site.db --cycles 3
```

The included profile name is historical; treat it as an example adapter and replace it with a profile for your own website.

## Research boundary

`research-news` stages a draft by default. Publishing is deliberately blocked unless the verifier finds two independent entries from the trusted-source registry with the required term overlap.

Copy `config/source-registry.example.json` to a private configuration file and edit the approved domains. If using the built-in search adapter, set `TAVILY_API_KEY` in your environment; never commit that key.

```json
{
  "title": "Example announcement",
  "publishDate": "2026-09-16",
  "summary": "A short, public summary.",
  "tags": ["announcement"],
  "query": "artist official announcement",
  "requiredTerms": ["artist", "announcement"]
}
```

```bash
project-os research-news \
  --site /path/to/site/src/layouts/BaseLayout.astro \
  --db .agent-data/site.db \
  --proposal proposal.json \
  --source-registry config/source-registry.json \
  --draft-dir outputs/news-drafts
```

Pass `--publish-to /path/to/site/src/content/news` only after a result is `verified`.

## Architecture

LangGraph carries only the execution context for one run. Raw content, evidence, observations and decision history stay in SQLite, so a restart does not erase the audit trail. The approval policy is simple: a candidate must improve weighted quality and must not lower correctness.

At project scope, future coding agents must produce a `ChangeSet`: each edited file includes its expected SHA-256 digest, so a concurrent user edit rejects the candidate instead of being overwritten. The applier rejects path traversal and writes each accepted replacement atomically.

Future work includes a browser collector, screenshot-based visual evaluator, a sandboxed coding agent, preview-deployment comparison, evaluator calibration, backups and migrations for multi-process use.

## Development

Run the test suite after changes:

```bash
python -m unittest discover -s tests -t . -v
```

Please keep new evaluators independent from the generator they assess, preserve the reject path, and never add credentials or private research data to the repository.

## License

MIT. See [LICENSE](LICENSE).
