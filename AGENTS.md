# AI Agent Workspace Guidelines (AGENTS.md)

Welcome, AI Agent. This workspace follows a unified protocol adapted from the IWAS (getiwas) agent operating model, specialized for the **Freqtrade MTF Sniper + JEV** trading suite.

---

## 1. Core Mission

Pair-program with the user on a **systematic crypto futures** bot:

- Strategies: `StrategyMTFSniper` (long+short), `StrategyMTFShortSniper` (short-only)
- Optional AI evaluation: JEV Hunter + Decision Gate (Typesafe), default-proven path is **native rules**
- Deploy focus: branch `pi-deploy`, Docker on Pi
- Pairs: BTC/USDT:USDT, ETH/USDT:USDT · leverage target 10x · TF 15m + MTF 1H/4H/1D

Persistent context lives in `memory-bank/`. Code intelligence lives in **GitNexus** (repo name: `freqtrade`).

---

## 2. Non-Negotiable Rules

### Rule 1: No automatic commits
Never commit/stage unless the user explicitly asks. For large trees, propose Conventional Commit batches and wait for approval.

### Rule 2: English in code / technical docs
Code, comments, API/design notes: English. Vietnamese OK for user chat.

### Rule 3: Evidence over narrative
Do not treat archived `reports/**/REPORT.md` headline PnL as ground truth without reproduction. Prefer local backtests, logs, and tests.

### Rule 4: GitNexus-first scoping (non-bypassable)
Never start with blind whole-repo grepping for architecture/callers.

```bash
node .gitnexus/run.cjs query -r freqtrade "<Concept or Symbol>"
node .gitnexus/run.cjs impact -r freqtrade "<SymbolName>"
node .gitnexus/run.cjs trace -r freqtrade "<From>" "<To>"
node .gitnexus/run.cjs detect-changes --scope all --repo .
# or: gitnexus status | analyze | …
```

Tier-2 ripgrep only for literals/config/env/non-AST files with bounded paths.

### Rule 5: JEV mode hygiene
- Mode switch: **`JEV_MODE`** only (`native` | `external` | `backtest`)
- Working external model alias: **`jev-latest`**
- Backtests: force `JEV_MODE=native` (or `backtest`) for determinism
- Do not invent unverified `jev-hunter-*` model IDs

---

## 3. Workflow Rituals

```
1. SESSION / TASK START
   • gitnexus status (reanalyze if stale)
   • Read memory-bank/activeContext.md + progress.md
   • Explore via GitNexus query/context/trace

2. PRE-EDIT IMPACT
   • impact on target symbol; warn on HIGH/CRITICAL; UNKNOWN ≠ safe

3. IMPLEMENTATION
   • Prefer small diffs; keep trading risk assumptions explicit

4. PRE-COMMIT GATE
   • Run relevant tests (e.g. unittest tests.strategy.test_jev_architecture)
   • detect_changes
   • Update memory-bank/activeContext.md + progress.md at milestone boundary

5. COMMIT (only if user asked) + REFRESH
   • Conventional Commits
   • gitnexus analyze
```

---

## 4. Memory Bank (`memory-bank/`)

| File | Purpose |
|------|---------|
| `projectbrief.md` | Goals / scope |
| `productContext.md` | Why / UX |
| `activeContext.md` | Current focus / next steps |
| `systemPatterns.md` | Architecture / contracts |
| `techContext.md` | Stack / commands / env |
| `progress.md` | Status / gaps / roadmap |

Update `activeContext.md` + `progress.md` **milestone-by-milestone**, not per micro-edit. Archive old material under `memory-bank/archive/`.

Cursor rules: `.cursor/rules/memory-bank.mdc`, `.cursor/rules/gitnexus.mdc`  
Hooks: `.cursor/hooks.json` (sessionStart bootstrap + post-`git commit` reminder)

---

## 5. Skills layout (IWAS symlink protocol)

- **Canonical:** `.agents/skills/gitnexus-*`
- **Symlinks only:** `.claude/skills/*` and `.cursor/skills/*` → `../../.agents/skills/...`
- Never commit duplicate physical skill trees under client folders. If `gitnexus analyze` recreates physical dirs, convert back to symlinks.

---

## 6. Token efficiency

- Prefer GitNexus compact graphs over huge ripgrep dumps
- Read large files by line range
- Keep Memory Bank lean; archive aggressively

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **freqtrade** (11580 symbols, 32436 relationships, 739 execution flows).

> Index stale? Run `node .gitnexus/run.cjs analyze --index-only` from the project root — it auto-selects an available runner. No `.gitnexus/run.cjs` yet? Bootstrap with `npx`, `bunx`, or `pnpm dlx` — e.g. `bunx gitnexus@latest analyze` (npm 11 npx crash; #1939).

## Always Do

- **MUST run impact before editing.** Use `impact({target: "symbolName", direction: "upstream"})` or `node .gitnexus/run.cjs impact "symbolName" --direction upstream --repo .`; report callers, processes, and risk. Never substitute grep for graph analysis.
- **MUST analyze graph changes before committing.** Use `detect_changes({scope: "all"})` (MCP) or `node .gitnexus/run.cjs detect-changes --scope all --repo .` (CLI fallback). `partial: true` or `truncated: true` is not a clean check — a zero means unseen, not unaffected; re-run it. For regression review: `detect_changes({scope: "compare", base_ref: "develop"})` or `node .gitnexus/run.cjs detect-changes --scope compare --base-ref "develop" --repo .`.
- MUST warn on HIGH/CRITICAL `risk` pre-edit; never use `riskSharedAxes` to waive a HIGH/CRITICAL `risk` warning. Compare File/symbol: MCP File omits axes; Graph-RAG expands File.
- **MUST treat `risk: UNKNOWN` as unresolved, not as low.** An empty caller set is not evidence the symbol is unused — it can also mean the callers are not resolvable by the index (plain-object property access, dynamic dispatch, cross-language calls). `impact` pairs `UNKNOWN` with a `riskNote` saying so. Confirm with a text search before treating the symbol as safe to change or delete; do not proceed on the strength of a zero.
- **MUST use `query({search_query: "concept"})` for concepts/flows, `context({name: "symbolName"})` for a named symbol, or `impact` for blast radius, on read-only callers, dependencies, imports, or execution flow.** Graph first; text search only for empty/`UNKNOWN`/literals.
- For security review, `explain({target: "fileOrSymbol"})` lists taint findings (source→sink flows; needs `analyze --pdg`).

## Never Do

- NEVER edit a function, class, or method before MCP/CLI impact analysis.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis, and never read `UNKNOWN` as an all-clear — it means the walk could not answer, which is the one verdict that requires confirming by other means.
- NEVER rename symbols with find-and-replace — use `rename` which understands the call graph.
- NEVER commit before MCP/CLI graph change analysis.

## Resources

| Resource | Use for |
| --- | --- |
| `gitnexus://repo/freqtrade/context` | Codebase overview, check index freshness |
| `gitnexus://repo/freqtrade/clusters` | All functional areas |
| `gitnexus://repo/freqtrade/processes` | All execution flows |
| `gitnexus://repo/freqtrade/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
| --- | --- |
| Understand architecture / "How does X work?" | `.agents/skills/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.agents/skills/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.agents/skills/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.agents/skills/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.agents/skills/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.agents/skills/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
