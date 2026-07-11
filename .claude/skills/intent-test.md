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

### Single-turn (Layer 3 — Keywords)

Generate test cases for each detection function:
- **Positive**: exact keywords, paraphrases, natural variations
- **Adversarial**: negation, empty, off-topic, false positives
- **Boundary**: conflicting inputs, mixed signals

Write to `tests/generated/{name}.json`.

### Multi-turn (Layer 2 — FSM)

Generate scenarios that cover ALL state transitions, including **state injection** — start from ANY state, not just idle:

```json
{
  "name": "from_confirm_exit_accept",
  "initial_state": {
    "session": {"status": "confirm_exit", "mode": "", "turns": 0, "messages": []}
  },
  "turns": [
    {"input": "好的", "expect": {"status": "idle", "handled": true}}
  ]
}
```

Goal: every state as a starting point, every transition covered.

Write to `tests/generated/{name}_multi.json`.

### Routing (Layer 1 — LLM)

Generate test cases with `expect_routing` — what the router should decide:

```json
{
  "id": "plan_related_yes",
  "input": "帮我制定学习计划",
  "expect_routing": {"is_plan_related": true}
}
```

Write to `tests/generated/{name}_layer1.json`.

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
