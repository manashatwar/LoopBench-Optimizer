# Design — LoopBench Accuracy & Language-Agnostic Upgrades

Status: Draft (design only — tasks.md to follow)
Author: manashatwar
Scope: `openevolve/optimizer_loop.py`, `openevolve/repo_mapper/*`, `sandbox/runner.py`,
`sandbox/entrypoint.sh`, `sandbox/Dockerfile.sandbox`, `loopbench/hero.py`, `loopbench/cli.py`

---

## 1. Context & motivation

LoopBench today is a working, evaluator-gated optimization loop (an open-source
instance of the AlphaEvolve/FunSearch pattern). Research and our own runs surfaced
four concrete gaps between "it works" and "it's a robust, language-agnostic system
that produces accurate results":

1. **Measurement accuracy.** Each candidate is timed with a *single* run. We observed
   443 ms vs 460 ms jitter on identical code. A single timing can't distinguish a real
   speedup from measurement noise — the exact failure the performance-optimization
   literature (PERFOPT-Bench, Kalibera & Jones) warns about ("gains must be
   reproducible, not measurement artifacts").
2. **Localization.** The biggest documented failure of LLM code optimizers
   (SWE-fficiency) is *finding the hot path*, not editing it. We give the LLM repo
   context and failure history, but no execution profile of where time actually goes.
3. **Context quality & language-agnosticism (analysis layer).** Our `RelevanceScorer`
   ranks by Python-import heuristics at the *file* level. It is not symbol-level, not
   importance-ranked, and not language-agnostic (won't parse Go/Rust/JS imports).
4. **Execution language-agnosticism.** The sandbox is a Python-only image and the
   entrypoint's score parser requires `python3`, so only Python (and pip-installable
   Python-hosted DSLs like Vyper) can actually be executed and verified.

This design addresses all four while preserving the core guarantee that makes LoopBench
trustworthy: **a candidate ships only if it is verified faster AND still correct.**

### Guiding constraints (from research)

- Long-context degradation ("lost-in-the-middle", context rot): more context ≠ better.
  Curate and place well; do not dump whole repos.
- Agentic autonomy has poor per-call cost/effectiveness vs. grounded sampling. Keep the
  LLM as a *mutation operator* grounded by deterministic tools — do not build an
  autonomous multi-agent system.
- LoopBench is cost-bounded and runs many LLM calls (generations × candidates). Any new
  context must be **computed once, cached, and reused** — only small deltas may change
  per call.

---

## 2. Goals / Non-goals

### Goals
- G1. Replace single-shot timing with a **statistical speed gate**: a candidate wins
  only when its improvement holds beyond measurement noise.
- G2. Add **profiler grounding**: feed the top hot spots of the target into the prompt,
  cheaply, to improve localization.
- G3. Keep per-call context bounded across many loops via a **cached static prefix +
  dynamic delta** prompt structure.
- G4. Upgrade context ranking to a **tree-sitter symbol graph + PageRank** approach
  (Aider's method), behind the existing `ContextMap` interface, with the current mapper
  as a fallback. This also makes the analysis layer language-agnostic.
- G5. Make execution **language-agnostic** via a pluggable `sandbox.image` and a
  toolchain-agnostic entrypoint.

### Non-goals
- N1. No autonomous/multi-agent tool-calling loop. The LLM stays a mutation operator.
- N2. No distributed/remote sandbox service in this iteration (local Docker + optional
  CI matrix only).
- N3. No change to the fundamental scoring formula semantics
  (`combined_score = correctness × speed_score`), only how `speed` is *measured*.
- N4. No rewrite of the generation modes (full-rewrite / search-replace / auto stay).

---

## 3. Current architecture (baseline)

```
loopbench run → hero.py → OptimizerLoop.run()
  establish_baseline() → run_in_sandbox() [1 timed run]
  for gen in 1..N:
    execute_generation():
      map    → RepoContextMapper.get_context_map()  (import-heuristic, file-level)
      gen    → LLMEnsemble (full / search_replace)
      apply  → git worktree + difflib patch
      test   → run_in_sandbox() [1 timed run] → score.json (single speed_ms)
      record → CandidateDatabase (SQLite)
      select → AutoEscalationSearch
```

Key files: `optimizer_loop.py` (loop + prompts), `repo_mapper/relevance_scorer.py`
(ranking), `sandbox/runner.py` + `entrypoint.sh` (execution + scoring),
`Dockerfile.sandbox` (Python image).

---

## 4. Detailed design

### 4.1 Statistical speed gate (G1)

**Problem.** One timed run → noise indistinguishable from signal.

**Design.** Introduce repeated measurement + a confidence-based decision:

- The sandbox runs the speed workload **K times** (default K=5, configurable
  `sandbox.repeats`) and reports a distribution, not a scalar. Two options, chosen by
  workload type:
  - **In-test repeats (default):** the evaluator already prints `LOOPBENCH_SPEED_MS`;
    run the whole test command K times and collect K markers. Reuses the existing
    contract, language-agnostic.
  - **`hyperfine` mode (opt-in, process-level):** when `sandbox.benchmark: hyperfine`,
    wrap the workload with hyperfine (warmup + auto-repeat + mean/stddev). Requires the
    hyperfine binary in the image.
- `score.json` gains: `speed_ms_median`, `speed_ms_mean`, `speed_ms_stddev`,
  `speed_ms_samples: [...]`, `runs: K`. `speed_ms` stays = median (back-compat).
- **Ship decision (in `OptimizerLoop`):** a candidate is accepted as a new best only if
  its **median beats the current best by a margin that clears the noise band** — e.g.
  the candidate's and baseline's 95% intervals (median ± z·stddev/√K) do **not** overlap,
  OR median improvement ≥ `min_effect` (config, default 3%). This is the "revalidate"
  principle applied to speed.
- **Optional final revalidation:** after the loop, re-run the winning patch M times
  (`--revalidate`) and only keep the "successful" status if the gain still holds.

**Files:** `sandbox/entrypoint.sh` (K-run loop + aggregation), `sandbox/runner.py`
(pass `repeats`, parse distribution), `optimizer_loop.py` (`_score_from_metrics` +
best-selection uses the confidence check), config passthrough in `hero.py`/`cli.py`.

**Config:**
```yaml
sandbox:
  repeats: 5             # timed runs per candidate
  benchmark: in-test     # in-test | hyperfine
metric:
  min_effect: 0.03       # min median improvement to count as a real win
```

**Back-compat:** `repeats: 1` reproduces today's behavior; `speed_ms` field unchanged.

---

### 4.2 Profiler grounding + cached-prefix prompt structure (G2, G3)

**Problem.** LLM lacks a "where is the time spent" signal (localization). Naively adding
profile data to every call inflates cost and risks lost-in-the-middle.

**Design.**

- **Profile once per run/file, not per generation.** During `establish_baseline`, run
  the workload under a profiler (Python: `cProfile`/`pyinstrument`; other languages via
  the pluggable image + a `sandbox.profile_command`). Extract a **compact hotspot
  summary**: top ~5 functions/lines by self-time, formatted as a few lines.
- **Cache the static context prefix.** Assemble the prompt as:
  - **Static prefix (computed once, reused every generation):** target file, curated
    neighbors (from the mapper), and the hotspot summary.
  - **Dynamic delta (per generation):** current baseline metrics, last N failed attempts,
    the current best diff.
- **Placement:** put the target file + hotspots at the **start/end** of the prompt
  (edges the model reads best), never buried mid-context.
- **Provider prompt caching:** structure the request so the static prefix is a stable
  cache prefix (Anthropic/Gemini/OpenAI caching), making repeated context cheap across
  generations. Degrades gracefully to plain prompts if the provider lacks caching.

**Files:** new `openevolve/profiler.py` (run profiler in sandbox, parse hotspots),
`repo_mapper/optimizer_prompt.py` (prefix/delta split + hotspot section),
`optimizer_loop.py` (compute hotspots once in baseline, pass to prompt builders),
`sandbox/runner.py` (optional profile execution).

**Config:**
```yaml
sandbox:
  profile: true                 # capture a hotspot summary at baseline
  profile_command: null         # override for non-Python (else auto)
prompt:
  cache_static_prefix: true     # use provider prompt caching when available
  max_hotspots: 5
```

**Cost note:** grounding adds ~a few hundred tokens to a *cached* prefix, sent once;
per-generation cost is dominated by the small delta. Net effect on multi-loop cost is
minimal — the opposite of a naive per-call dump.

---

### 4.3 Tree-sitter + PageRank context mapper (G4)

**Problem.** `RelevanceScorer` is file-level, import-heuristic, Python-only.

**Design.** Adopt Aider's proven approach behind the existing interface:

- New ranker builds a **symbol graph** with **tree-sitter** (definitions + references
  across files), runs **PageRank** to rank symbols/files by importance, and emits the
  same `ContextMap` object the loop already consumes.
- Language-agnostic: tree-sitter grammars cover Python, JS/TS, Go, Rust, Java, etc., so
  the *analysis* layer stops being Python-only.
- **Interface-preserving:** `RepoContextMapper.get_context_map()` signature unchanged.
  Add a strategy switch: `mapper.engine: pagerank | import-heuristic (default: auto)`.
- **Fallback:** if tree-sitter grammar for the language is missing or parsing fails,
  fall back to the current `RelevanceScorer`. No hard dependency break.
- **Reuse OSS:** `grep-ast` + `tree_sitter_languages`, or vendor the ranking logic from
  the MIT `pdavis68/RepoMapper` port. Keep it optional (extra dependency group).

**Files:** new `openevolve/repo_mapper/pagerank_ranker.py`, wire into
`repo_mapper/mapper.py` behind a config flag; `RelevanceScorer` untouched (fallback).

**Config:**
```yaml
mapper:
  engine: auto            # auto | pagerank | import-heuristic
  token_budget: 3000
```

**Back-compat:** default `auto` uses pagerank when tree-sitter is available for the
detected language, else the current heuristic — existing Python runs behave the same or
better.

---

### 4.4 Pluggable sandbox image + toolchain-agnostic entrypoint (G5)

**Problem.** Execution is Python-locked: `FROM python:3.12-slim`, and the entrypoint's
score parser is a `python3` block.

**Design.**

- **`sandbox.image`** (config) selects the base image (default: the built
  `loopbench-sandbox` Python image). `sandbox.setup: [...]` runs pre-steps baked into a
  cached layer (replacing the pip-only path with a generic setup for any language).
- **Toolchain-agnostic entrypoint:** branch the scorer —
  - pytest path → keep the `python3` JSON parse (Python guaranteed present).
  - generic path → compute the score in **POSIX shell + awk** (exit code = correctness;
    `grep` the `LOOPBENCH_SPEED_MS` marker(s); `awk` computes `exp(-ms/150)` and the
    median across K runs). No `python3` dependency in non-Python images.
- `runner.py` `_resolve_image` / build logic generalized: `base = sandbox.image or
  default`, layer = `setup` commands (or pip deps for the Python default).

**Files:** `sandbox/Dockerfile.sandbox` (unchanged default), `sandbox/entrypoint.sh`
(shell/awk scorer branch), `sandbox/runner.py` (image + setup resolution),
`hero.py`/`cli.py` (config passthrough).

**Config:**
```yaml
sandbox:
  image: "rust:1.82-slim"       # optional; default = python sandbox
  setup: ["cargo fetch"]        # optional pre-build steps
  command: "cargo bench ..."
```

**Back-compat:** no `sandbox.image` → the current Python image and Python scorer path,
unchanged. Vyper/Python runs are unaffected.

---

## 5. Cross-cutting concerns

- **Determinism / reproducibility:** repeats + median + fixed seeds where possible;
  statistical gate makes "successful" runs reproducible by construction.
- **Cost:** the cache-prefix + delta structure and compact hotspots keep per-call tokens
  roughly flat even as grounding grows; repeats add sandbox time (not LLM cost).
- **Security:** unchanged — every candidate still runs `--network=none`, read-only, in an
  isolated git worktree.
- **Backward compatibility:** every feature is behind a config flag defaulting to current
  behavior. Existing example runs must produce equal-or-better results.

## 6. Risks & mitigations

| Risk | Mitigation |
|------|-----------|
| Repeats increase wall-clock per candidate | K configurable; default 5; parallelizable later |
| tree-sitter adds a heavy dependency | Optional extra; `auto` falls back to current mapper |
| Prompt caching varies by provider | Feature-detect; degrade to plain prompt |
| Non-Python entrypoint scorer edge cases | Keep pytest path intact; add shell/awk path with tests |
| Profiler unavailable for a language | `profile: false` skips grounding; loop still runs |

## 7. Testing strategy

- Unit: distribution parsing in `runner.py`; confidence gate in `optimizer_loop`;
  awk scorer output equals python scorer on the same inputs; pagerank ranker returns a
  valid `ContextMap`; fallback path when tree-sitter missing.
- Integration (Docker): K-run scoring on the bubble-sort example; a non-Python image
  smoke test (e.g. a trivial Node or Go target) proving the generic scorer path.
- Regression: existing Python examples must still pass and score equal-or-better.
- Property: statistical gate never accepts a candidate whose interval overlaps baseline.

## 8. Rollout / phasing

Recommended order (highest ROI first):
1. **Phase 1 — Statistical speed gate (4.1).** Protects the core "verified" promise.
2. **Phase 2 — Profiler grounding + cached prefix (4.2/4.3-prompt).** Localization.
3. **Phase 3 — Pluggable sandbox image + generic scorer (4.4).** Language-agnostic exec.
4. **Phase 4 — tree-sitter + PageRank mapper (4.3).** Deep, language-agnostic context.

Each phase is independently shippable and flag-guarded.

## 9. Open questions

- Default K (repeats) — 5 vs 7 for a good noise/time tradeoff?
- Confidence method — non-overlapping z-intervals vs a fixed `min_effect` vs both?
- tree-sitter dependency — vendor a minimal ranker vs depend on `grep-ast`?
- Should the final `--revalidate` be on by default for "successful" runs?
