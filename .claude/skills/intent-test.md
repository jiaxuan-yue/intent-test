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

| Architecture | Signs | runner.py adapter | Adapter class |
|-------------|-------|-------------------|---------------|
| **Function-based dialog** | `dialog.py`, `_detect_*_intent()`, `_is_yes()`, `handle_plan_chat()` | `--adapter dialog` | `DialogFunctionsAdapter` |
| **Class-based engine** | `class RuleEngine`, `engine.match(input)` | `--adapter rule_engine` | `RuleEngineAdapter` |
| **LLM structured output** | `ExecutionPlan`, prompt chains, `BaseModel` | `--adapter llm_analyzer` | `LLMAnalyzerAdapter` |
| **Two-layer hybrid** | LLM `analyzer_node` + keyword `dialog.py` | `--adapter dialog` (Layer 2) | `DialogFunctionsAdapter` |
| **Custom** | Any other architecture | `--adapter custom` | `CustomAdapter` |
| **Auto-detect** | Unknown | `--adapter auto` (default) | tries dialog → rule_engine |

All adapters implement `BaseAdapter` interface:
- `detect_intents(input, context)` — unified detection result
- `get_keyword_sets()` — keywords for auto test generation
- `get_all_functions()` — list of testable functions
- `get_handle_fn()` — multi-turn handler if available

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

runner.py supports five adapter types via class-based `BaseAdapter` architecture:
- `--adapter dialog` — `DialogFunctionsAdapter`: auto-loads `dialog.py`, mocks heavy deps, tests all `_detect_*` functions + multi-turn `handle_plan_chat()`
- `--adapter rule_engine` — `RuleEngineAdapter`: imports `RuleEngine` class
- `--adapter llm_analyzer` — `LLMAnalyzerAdapter`: loads LLM structured output via adapter-path
- `--adapter custom --adapter-path adapter.py` — `CustomAdapter`: user-provided `match()` function
- `--adapter auto` (default) — tries `DialogFunctionsAdapter` → `RuleEngineAdapter`

For new architectures, write a ~50 line adapter implementing `BaseAdapter`.

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
| **Multi-turn** | Conversation context shifts, FSM state transitions | 12 scenarios | P1 |
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

#### Multi-Turn State Machine Testing

For projects with dialog state machines (e.g., `handle_plan_chat()` with FSM states), use the dedicated multi-turn commands:

```bash
# Generate 12 built-in FSM scenarios (covers all state transitions)
python .claude/skills/intent-test/runner.py generate_multi --name plan_fsm

# Execute multi-turn scenarios
python .claude/skills/intent-test/runner.py run_multi \
  --suite tests/generated/plan_fsm_multi.json \
  --adapter dialog \
  --output tests/generated/plan_fsm_multi_report.json

# Display state transition coverage report
python .claude/skills/intent-test/runner.py report_multi \
  --results tests/generated/plan_fsm_multi_report.json
```

**How it works:**
- 12 built-in scenarios cover all 6 FSM states and major transition paths
- Each scenario chains multiple turns, passing `plan_session` between calls
- LLM dependencies are mocked: `_get_chat_model()` raises → fallback to `_next_default_question()`
- `asyncio.run()` wraps async `handle_plan_chat()` calls
- Validates status, mode, handled flag, and turn count after each turn

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

5. **State transition coverage** (multi-turn tests):
   ```
   State Transition Coverage:
     idle → collecting:         3/3 ✅
     collecting → await_exit:   2/2 ✅
     await_exit → idle:         1/1 ✅
     await_exit → collecting:   1/1 ✅
     ...
   Uncovered: 2 transitions
   ```
   Warn if any major transition path has zero coverage.

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
| `{name}.json` | Single-turn test suite definition |
| `{name}_report.json` | Single-turn test results with timing |
| `{name}_multi.json` | Multi-turn FSM scenario definitions |
| `{name}_multi_report.json` | Multi-turn results with state transition coverage |
| `{name}_summary.md` | Markdown analysis report |
| `{name}_fixes.md` | Applied fixes changelog (if auto-fix ran) |

## Parameters

Parse from user's skill invocation (e.g., `/intent-test mode=analyze auto_fix=true`):

| Parameter | Default | Description |
|-----------|---------|-------------|
| `mode` | `analyze` | `generate` / `run` / `analyze` / `quick` / `fix` / `research` / `generate_multi` / `run_multi` / `analyze_multi` / `check_deps` / `fsm_coverage` |
| `intents` | auto-detect | JSON string of custom intents (fallback if no code found) |
| `name` | `intent_tests` | Suite name |
| `output_dir` | `tests/generated` | Output directory |
| `auto_fix` | `false` | Apply fixes automatically |
| `dry_run` | `true` | Preview fixes without applying |
| `max_fixes` | `5` | Maximum fixes to apply |
| `input` | — | Single input for `quick` mode |
| `context` | — | State context JSON for `quick` mode (e.g. `'{"status": "collecting"}'`) |
| `regression_file` | — | Path to regression test cases |
| `performance_threshold` | — | Max response time in ms |

## Quick Mode

For `mode=quick`:
1. Use existing adapter or auto-detect
2. Call `adapter.detect_intents(input, context)` on the provided input
3. Show: detected intent, confidence, matched keywords/patterns
4. If confidence < 0.5, suggest LLM fallback or rule improvement

**Context-aware testing** — test detection in a specific dialog state:
```bash
python .claude/skills/intent-test/runner.py quick \
  --input "继续" --context '{"status": "await_exit_confirm"}' --adapter dialog
```

## Utility Commands

### check_deps — Dependency diagnostics

Check which project dependencies are available, mocked, or missing:
```bash
python .claude/skills/intent-test/runner.py check_deps
```
Mocked: langchain_core, langgraph, langchain_openai, langchain_deepseek, openai, anthropic, pydantic.

### fsm_coverage — FSM state transition analysis

Analyze the project's state machine and show coverage:
```bash
python .claude/skills/intent-test/runner.py fsm_coverage --adapter dialog
```

## Exit Codes

| Code | Meaning |
|------|---------|
| `0` | All tests passed |
| `1` | Some failures, pass_rate ≥ 0.5 |
| `2` | Heavy failures, pass_rate < 0.5 |
| `3` | Runtime error (import failure, etc.) |

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
