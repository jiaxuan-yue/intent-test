---
name: intent-test
description: "Automated testing for any intent recognition system — auto-discovers your architecture (LLM-driven, rule-based, function-based, hybrid), generates 7 types of test cases, runs tests, analyzes failures, and suggests fixes. Use when: testing intent recognition accuracy, adding new intents, optimizing rules, discovering edge cases, regression testing, or CI/CD intent validation."
---

# Intent Recognition Testing

Test **any** intent recognition system end-to-end: discover → generate → execute → analyze → fix.

Works with all architectures: rule engines, LLM analyzers, keyword matchers, dialog state machines, function-based classifiers, and hybrid systems.

## Workflow

### Phase 1: Auto-Discover the Intent System

**Step 1 — Broad file discovery.** Scan the project to find intent-related code:

```bash
# File names
find . -type f -name "*.py" | grep -iE "intent|engine|recogni|classify|nlp|router|dialog|analyzer|agent|plan"

# Config files with intent definitions
find . -type f \( -name "*.json" -o -name "*.yaml" -o -name "*.yml" \) | grep -iE "intent|rule|keyword|config"

# Directory names
find . -type d | grep -iE "intent|engine|nlp|recogni|dialog|agent"
```

**Step 2 — Keyword scan.** Grep for intent-related code patterns:

```bash
grep -rl "intent\|keyword_rule\|match.*intent\|recognize\|classify\|detect_intent\|ExecutionPlan\|plan_intent" --include="*.py" .
```

**Step 3 — Read and classify architecture.** Read discovered files and determine which architecture type(s) the project uses:

| Architecture | Signs | runner.py adapter | How to call |
|-------------|-------|-------------------|-------------|
| **Class-based engine** | `class RuleEngine`, `engine.match(input)` | `--adapter rule_engine` | Instantiate class, call `.match()` |
| **Function-based dialog** | `dialog.py`, `_detect_*_intent()`, `_is_yes()`, `_is_no()` | `--adapter dialog` | Call each detection function individually |
| **Two-layer hybrid** | LLM `analyzer_node` + keyword `dialog.py` | `--adapter dialog` (Layer 2) | Test keyword layer directly; LLM layer needs manual review |
| **LLM-only analyzer** | `ExecutionPlan`, prompt chains, no keyword rules | `--adapter custom` | Generate adapter that calls LLM analyzer |
| **Config-driven** | JSON/YAML keyword→intent mapping | `--adapter custom` | Load config, implement matching in adapter |

**Step 4 — Build intent model.** Extract:
- All intent names/enums (from code or config)
- Keywords mapped to each intent
- Matching logic (exact, regex, fuzzy, embedding, LLM)
- Confidence scoring mechanism
- Fallback behavior (None? LLM call? clarification?)
- Context/state dependencies (does recognition depend on dialog state?)

For **two-layer architectures** (e.g., ChatTutor):
- **Layer 1 (LLM Analyzer)**: `analyzer_node` → outputs `ExecutionPlan` with boolean fields. Cannot be unit-tested without LLM API access — note this as a limitation.
- **Layer 2 (Keyword Dialog)**: `dialog.py` → `_detect_plan_intent()`, `_is_learn_intent()`, `_is_yes()`, `_is_no()`, `_is_exit_intent()`, etc. Fully testable with runner.py.
- **Testing strategy**: Use `--adapter dialog` to test Layer 2 exhaustively. For Layer 1, generate test inputs and document expected LLM outputs for manual/CI review.

**Step 5 — Choose adapter and generate if needed.**

runner.py supports three built-in adapter types:
- `--adapter dialog` — auto-loads `dialog.py`, mocks heavy deps (langchain_core, langgraph), tests all `_detect_*` functions
- `--adapter rule_engine` — imports `RuleEngine` class from `intent_recognition.engine`
- `--adapter custom --adapter-path adapter.py` — loads user-provided adapter
- `--adapter auto` (default) — tries dialog first, then rule_engine

For architectures not covered by built-in adapters, generate `tests/generated/adapter.py`:
```python
def match(input_text: str) -> dict:
    """Returns: {"intent": str|None, "confidence": float, "details": {...}}"""
```

**Step 6 — Fallback if nothing found.** If Steps 1-4 find zero intent code:
- Ask user to point to the module or provide intents as JSON
- Generate adapter from user-provided intents using simple keyword matching

### Phase 2: Generate Test Cases

Generate test cases across 7 categories. For each intent, produce cases proportionally:

| Type | Purpose | Count per intent | Priority |
|------|---------|-----------------|----------|
| **Positive** | Correct recognition with clear keywords | 3-5 | P0 |
| **Boundary** | Ambiguous inputs, multi-intent overlap | 2-3 | P1 |
| **Adversarial** | Negations ("不想学"), typos ("pythn"), irrelevant noise | 3-5 | P1 |
| **Regression** | Previously failed cases (load from file if available) | All | P0 |
| **Performance** | Response time under threshold | 1-2 | P2 |
| **Multi-turn** | Conversation context shifts | 2-3 | P2 |
| **Context-aware** | State-dependent recognition | 1-2 | P2 |

**Generation rules:**
- Positive: exact keywords, paraphrases, natural variations
- Boundary: inputs matching multiple intents, partial keyword matches
- Adversarial: negation prefixes (不/没/非), character swaps, redundant text, unrelated inputs
- For Chinese intents: simplified/traditional variants, pinyin homophones
- For LLM-driven systems: test cases where keyword rules and LLM might disagree

Write test suite to `{output_dir}/{suite_name}.json`.

### Phase 3: Execute Tests

Run the test suite through the adapter. Choose the correct adapter flag based on Phase 1 discovery:

```bash
# Dialog/function-based projects (ChatTutor, etc.)
python .claude/skills/intent-test/runner.py run \
  --suite tests/generated/intent_tests.json \
  --adapter dialog \
  --output tests/generated/intent_tests_report.json

# Class-based RuleEngine projects
python .claude/skills/intent-test/runner.py run \
  --suite tests/generated/intent_tests.json \
  --adapter rule_engine \
  --output tests/generated/intent_tests_report.json

# Custom adapter
python .claude/skills/intent-test/runner.py run \
  --suite tests/generated/intent_tests.json \
  --adapter custom --adapter-path tests/generated/adapter.py \
  --output tests/generated/intent_tests_report.json

# Auto-detect (tries dialog → rule_engine → custom)
python .claude/skills/intent-test/runner.py run \
  --suite tests/generated/intent_tests.json \
  --output tests/generated/intent_tests_report.json
```

Display readable report:
```bash
python .claude/skills/intent-test/runner.py report --results tests/generated/intent_tests_report.json
```

### Phase 4: Analyze Results

Compute and present:

1. **Summary:** `Total: N | Passed: N (XX%) | Failed: N | Errors: N`

2. **Failure classification** by root cause:
   - 🔴 Confidence too low — matched correct intent but below threshold
   - 🟠 Wrong intent — keyword conflict between intents
   - 🟡 No match — system returned None/unrecognized
   - 🔵 LLM disagreement — keyword layer and LLM layer gave different results (hybrid systems)
   - 🟢 Timeout — exceeded performance threshold

3. **Keyword conflict matrix** — overlapping keywords between intents

4. **Coverage gaps** — which intents lack test coverage

### Phase 5: Suggest & Apply Fixes

Generate **architecture-aware** suggestions:

| Architecture | Fix types |
|-------------|-----------|
| Rule engine | Add keywords, adjust weights, add negation patterns |
| Function-based | Add detection cases, modify return logic |
| LLM-driven | Adjust prompts, refine output parsing, add guardrail rules |
| Dialog state machine | Add state transitions, fix context-dependent detection |
| Config-driven | Update keyword mappings, add conflict resolution rules |
| Hybrid | Fix keyword layer, adjust LLM fallback threshold, reconcile conflicts |

**Auto-fix** (if user requests):
1. Show preview of changes (dry-run first)
2. Apply changes using Edit tool to the project's actual source files
3. Re-run affected test cases to verify

## Output Files

Write all outputs to the configured output directory (default: `tests/generated/`):

| File | Content |
|------|---------|
| `adapter.py` | Auto-generated adapter for the project's intent system |
| `{name}.json` | Test suite definition |
| `{name}_report.json` | Raw test results with timing |
| `{name}_summary.md` | Markdown analysis report |
| `{name}_fixes.md` | Applied fixes changelog (if auto-fix ran) |

## Parameters

Parse from user's skill invocation (e.g., `/intent-test mode=analyze auto_fix=true`):

| Parameter | Default | Description |
|-----------|---------|-------------|
| `mode` | `analyze` | `generate` / `run` / `analyze` / `quick` / `fix` / `research` |
| `intents` | auto-detect | JSON string of custom intents (fallback if no code found) |
| `name` | `intent_tests` | Suite name |
| `output_dir` | `tests/generated` | Output directory |
| `auto_fix` | `false` | Apply fixes automatically |
| `dry_run` | `true` | Preview fixes without applying |
| `max_fixes` | `5` | Maximum fixes to apply |
| `input` | — | Single input for `quick` mode |
| `regression_file` | — | Path to regression test cases |
| `performance_threshold` | — | Max response time in ms |

## Quick Mode

For `mode=quick`:
1. Use existing adapter or generate one
2. Call adapter's `match(input)` on the provided input
3. Show: detected intent, confidence, matched keywords/patterns
4. If confidence < 0.5, suggest LLM fallback or rule improvement

## Research Mode

For `mode=research`, produce a comprehensive report:
- Coverage matrix (intent × test type)
- Failure root cause analysis with code references
- Architecture-specific improvement roadmap (short/mid/long term)
- Comparison with previous runs (if reports exist)

## Tips

- Start with `analyze` mode for first-time testing
- The adapter is auto-generated — review it before CI/CD use
- For hybrid systems, test both layers (keyword + LLM) independently
- After fixes, re-run with regression file to verify
- For CI/CD: check `pass_rate` in report JSON, fail if < threshold
