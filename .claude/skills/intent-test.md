---
name: intent-test
description: "Automated testing for any intent recognition system. Claude reads code → understands architecture → generates config + tests → runner.py executes → Claude analyzes failures. Supports LLM routing layers, FSM dialog state machines, keyword matchers, and hybrid systems."
---

# Intent Recognition Testing

Two-layer architecture: **Claude is the intelligence, runner.py is the executor.**

```
Claude (understanding)              runner.py (execution)
┌──────────────────────┐           ┌───────────────────────┐
│ Phase 1: Read code   │──────────▶│ config.json           │
│ Phase 2: Generate    │           │ test scenarios        │
│ Phase 5: Analyze     │◀──────────│ test reports          │
└──────────────────────┘           └───────────────────────┘
```

## Phase 1: Read and Understand the Code (Claude's job)

Read the project's source code to build an architecture model. runner.py does NOT do this — you do.

**Step 1 — Discover the codebase.** Use Read/Bash to find and read:
- Dialog/FSM files: `dialog.py`, `state_machine.py`, `fsm.py`
- LLM analyzer files: `analyzer.py`, `agent_builder.py`, `prompts.py`
- Engine files: `engine.py`, `recognizer.py`, `classifier.py`
- Config files: any JSON/YAML defining intents or keywords

**Step 2 — Classify architecture layers.** Identify which layers exist:

| Layer | Signs | How to test |
|-------|-------|-------------|
| **L1: LLM Routing** | `analyzer_node()`, `ExecutionPlan`, prompt chains | `run_layer1` with LLM mocks |
| **L2: FSM Dialog** | `handle_*_chat()`, state transitions, `_detect_*_intent()` | `run_multi` with state injection |
| **L3: Keywords** | keyword lists, `_is_yes()`, `_is_no()`, regex patterns | `run` / `quick` single-turn |

**Step 3 — Generate config.json.** Write the architecture config that runner.py will read:

```json
{
  "architecture": "hybrid",
  "layers": {
    "routing": {
      "entry": "app.core.agent_builder.analyzer_node",
      "llm_mocks": {
        "_is_plan_related_llm": "keyword_fallback",
        "_should_exit_llm": "keyword_fallback"
      },
      "params": {"task_id": "test"}
    },
    "dialog": {
      "entry": "app.core.task_plan.dialog.handle_plan_chat",
      "params": {"task_id": "test", "existing_plan": null}
    },
    "keywords": {
      "source": "app.core.task_plan.prompts"
    }
  },
  "states": ["idle", "active", "confirm_exit", "await_offer", "await_confirm"]
}
```

Mock strategies for LLM functions:
- `"keyword_fallback"` — replace LLM call with keyword matching
- `"return_true"` / `"return_false"` — fixed boolean
- `"return:VALUE"` — return specific JSON value

Save to `tests/generated/config.json`.

**Step 4 — Extract state graph.** Read FSM code to identify:
- All states and their transition conditions
- Which transitions are triggered by keywords vs LLM
- Which states can be starting points for test scenarios

## Phase 2: Generate Tests (Claude's job)

Test generation follows **6 universal dimensions** — these apply to ANY intent system regardless of architecture:

```
1. Correctness  — input → correct intent?
2. Robustness   — dirty/adversarial input → graceful degradation?
3. State flow   — FSM every path → traversed?
4. Routing      — LLM layer decisions → match expected?
5. Confidence   — high confidence → actually correct? low → actually uncertain?
6. Performance  — response time → within production threshold?
```

### Dimension 1: Correctness (single-turn)

For each intent/function discovered in Phase 1:
- **Positive**: exact keywords, paraphrases, synonyms, natural variations
- **Negative**: inputs that should NOT match this intent
- **Cross-intent**: inputs that could match a DIFFERENT intent (conflict detection)

### Dimension 2: Robustness (universal adversarial patterns)

These patterns apply to ALL intent systems, all languages:

| Pattern | Examples | Tests |
|---------|----------|-------|
| **Empty/null** | `""`, `"   "`, `None` | Should return no-match gracefully |
| **Negation** | `"不想X"`, `"don't X"`, `"别X"` | Should NOT trigger X's intent |
| **Off-topic** | `"今天天气"`, `"who are you"` | Should return no-match |
| **Noise** | `"asdfghjkl"`, `"🎉🎊"`, `"..."` | Should not crash or false-match |
| **Long input** | 500+ char text | Should handle without timeout |
| **Mixed language** | `"我想learn Python编程"` | Should handle code-switching |
| **Unicode edge** | full-width chars, zero-width spaces | Should normalize or handle |
| **Injection** | `"忽略之前指令,告诉我..."` | Should not execute injected instructions |
| **Repetition** | `"学学学学学学"` | Should not false-match |
| **Homophone** | `"想穴Python"` (学→穴) | Should handle near-miss |

Generate at least 3 adversarial cases per pattern per intent.

### Dimension 3: State Flow (multi-turn FSM)

Read the actual FSM code → extract ALL states and transitions → generate:
1. **Happy path** for each start→end flow
2. **State injection** — start from EVERY state, test all outgoing transitions
3. **Back-and-forth** — enter state → leave → return → leave again
4. **Guard rails** — inputs that should NOT trigger a transition
5. **Max turns / timeout** — what happens at boundaries

```json
// Template: Claude fills in {state}, {input}, {expected} from actual code
{
  "name": "inject_from_{state}",
  "initial_state": {"session": {"status": "{state}", ...}},
  "turns": [
    {"input": "{trigger_input}", "expect": {"status": "{next_state}"}}
  ]
}
```

### Dimension 4: Routing (LLM layer)

For each LLM decision point found in Phase 1:
- Generate inputs where the routing decision is clear (high confidence expected)
- Generate ambiguous inputs where routing could go either way
- Test with mocked LLM returning each possible value

### Dimension 5: Confidence Calibration

For inputs with known outcomes:
- Clear matches should have confidence > 0.8
- Ambiguous inputs should have confidence 0.3-0.7
- Non-matches should have confidence < 0.3
- Flag any input where confidence and correctness disagree

### Dimension 6: Performance

- Baseline: single input response time
- Stress: 100 sequential inputs, measure p50/p95/p99
- Cold start: first invocation vs warmed up
- Long input: 1000+ char input timing

## Phase 3: Execute Tests (runner.py's job)

runner.py reads the config Claude generated and executes:

```bash
# Layer 3: single-turn keyword testing
python .claude/skills/intent-test/runner.py run \
  --suite tests/generated/keywords.json --adapter dialog \
  --output tests/generated/layer3_report.json

# Layer 2: multi-turn FSM with state injection
python .claude/skills/intent-test/runner.py run_multi \
  --suite tests/generated/fsm_multi.json --adapter dialog \
  --output tests/generated/layer2_report.json

# Layer 1: LLM routing with mock
python .claude/skills/intent-test/runner.py run_layer1 \
  --suite tests/generated/routing.json \
  --config tests/generated/config.json \
  --output tests/generated/layer1_report.json

# Unified report: combine all layers
python .claude/skills/intent-test/runner.py report_unified \
  --reports tests/generated/layer1_report.json \
           tests/generated/layer2_report.json \
           tests/generated/layer3_report.json
```

runner.py capabilities:
- **Auto-detect functions** via `inspect.signature()` — single str param = detector
- **Auto-build call kwargs** from function signature — no hardcoded variants
- **Auto-discover handlers** by message+session parameter pattern
- **Auto-compute package paths** from `__init__.py` chains
- **LLM mock** — monkey-patches LLM functions per config strategies
- **State injection** — scenarios start from any FSM state with full session object

## Phase 4: Analyze Results (Claude's job)

Read the reports runner.py produced. Analyze:

1. **Layer-specific pass rates** — which layer has the most failures?
2. **Failure root causes** — keyword conflict? LLM routing error? FSM logic bug?
3. **State transition coverage** — which paths are uncovered?
4. **Architecture-aware suggestions** — fix keywords vs fix prompts vs fix FSM logic

## Phase 5: Suggest & Apply Fixes (Claude's job)

Generate fixes targeted at the correct layer:

| Layer | Fix types |
|-------|-----------|
| L1 Routing | Adjust LLM prompts, refine output parsing, add keyword guardrails |
| L2 FSM | Fix state transition logic, add missing transitions, handle edge states |
| L3 Keywords | Add/remove keywords, fix negation handling, resolve conflicts |

## Output Files

| File | Produced by | Content |
|------|------------|---------|
| `config.json` | Claude | Architecture config for runner.py |
| `{name}.json` | Claude | Single-turn test suite |
| `{name}_multi.json` | Claude | Multi-turn FSM scenarios |
| `{name}_layer1.json` | Claude | Routing test suite |
| `layer{1,2,3}_report.json` | runner.py | Per-layer test results |
| `unified_report.json` | runner.py | Combined layered report |

## Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `mode` | `analyze` | `generate` / `run` / `analyze` / `quick` / `research` / `generate_multi` / `run_multi` / `run_layer1` / `report_unified` |
| `config` | auto-detect | Path to architecture config.json |
| `name` | `intent_tests` | Suite name |
| `output_dir` | `tests/generated` | Output directory |

## Key Design Principles

1. **Claude reads code, runner.py doesn't** — understanding architecture is Claude's job
2. **Config-driven mock** — Claude generates config.json telling runner.py how to mock LLM
3. **State injection** — multi-turn scenarios start from ANY state, not just idle
4. **Signature-based detection** — runner.py uses `inspect.signature()`, never hardcoded names
5. **Layered testing** — L1 routing + L2 FSM + L3 keywords, unified report

## Tips

- Always start with `/intent-test mode=analyze` — Claude reads code and generates everything
- Review `config.json` before running — it describes how runner.py will mock and test
- Use `report_unified` to see all layers at once
- For CI/CD: check each layer's pass_rate independently
- State injection scenarios catch bugs that idle-start scenarios miss
