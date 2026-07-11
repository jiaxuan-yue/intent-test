---
name: intent-test
description: "Automated testing for intent recognition systems — generate test cases, run tests, analyze failures, suggest and apply fixes. Use when: testing intent recognition accuracy, adding new intents, optimizing rules, discovering edge cases, regression testing, or CI/CD intent validation."
---

# Intent Recognition Testing

Test any intent recognition system end-to-end: generate cases → execute → analyze → fix.

## Workflow

### Phase 1: Understand the System

1. Locate the intent recognition module. Search for:
   - `src/intent_recognition/engine/` or similar paths containing `RuleEngine`
   - Files matching `*engine*.py`, `*intent*.py`, `*recogni*.py`
   - Any `keyword_rules`, `intent_map`, or configuration JSON/YAML defining intents

2. Read the engine code to extract:
   - **Intent definitions** — all intent names/enums
   - **Keyword rules** — keywords mapped to each intent
   - **Confidence thresholds** — min/max confidence values
   - **Pattern matchers** — regex or template patterns
   - **Fallback behavior** — what happens on no match

3. If no engine code exists, ask the user to provide intents as JSON:
   ```json
   {"greeting": ["你好", "hi", "hello"], "farewell": ["再见", "bye"]}
   ```

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
- Positive cases: use exact keywords, paraphrases, and natural variations
- Boundary cases: inputs matching multiple intents, partial keyword matches
- Adversarial: inject negation prefixes (不/没/非), character swaps, redundant text, completely unrelated inputs
- For Chinese intents: include simplified/traditional variants and pinyin homophones

Write test cases to `{output_dir}/{suite_name}.json`:
```json
{
  "name": "intent_tests",
  "generated_at": "ISO-8601",
  "test_cases": [
    {
      "id": "pos_greeting_1",
      "input": "你好啊",
      "expected_intent": "greeting",
      "case_type": "positive",
      "priority": "p0"
    }
  ]
}
```

### Phase 3: Execute Tests

Run the test suite. Two approaches, pick based on project setup:

**Option A — Direct Python execution** (preferred if module is importable):
```python
import json, sys, time
sys.path.insert(0, "src")
from intent_recognition.engine import RuleEngine

engine = RuleEngine()
suite = json.load(open("tests/generated/intent_tests.json"))

results = []
for tc in suite["test_cases"]:
    start = time.time()
    result = engine.match(tc["input"])
    elapsed = (time.time() - start) * 1000
    results.append({
        "id": tc["id"],
        "input": tc["input"],
        "expected": tc["expected_intent"],
        "actual": result.intent.value if result.intent else None,
        "confidence": result.confidence,
        "elapsed_ms": round(elapsed, 2),
        "passed": (result.intent.value if result.intent else None) == tc["expected_intent"]
    })
```

**Option B — Use helper script**:
```bash
python .claude/skills/intent-test/runner.py run --suite tests/generated/intent_tests.json
```

### Phase 4: Analyze Results

Compute and present:

1. **Summary statistics:**
   ```
   Total: N  |  Passed: N (XX%)  |  Failed: N  |  Errors: N
   ```

2. **Failure classification** — group failures by root cause:
   - 🔴 **Confidence too low** — matched correct intent but confidence < threshold
   - 🟠 **Wrong intent** — matched a different intent (keyword conflict)
   - 🟡 **No match** — engine returned None/unrecognized
   - 🟢 **Timeout** — exceeded performance threshold

3. **Keyword conflict matrix** — identify overlapping keywords between intents:
   ```
   Conflict: "学习" matches both [start_plan] and [study_query]
   ```

4. **Coverage gaps** — which intents have no/insufficient test coverage

### Phase 5: Suggest & Apply Fixes

Generate prioritized suggestions:

| Priority | Action | Example |
|----------|--------|---------|
| 🔴 P0 | Add disambiguating keywords | Add "计划学习" to start_plan |
| 🟠 P1 | Adjust confidence thresholds | Lower threshold for greeting from 0.7 to 0.5 |
| 🟡 P2 | Add negation patterns | Add rule: "不想" + intent → negate |
| 🟢 P3 | Add fallback patterns | Add catch-all for unrecognized inputs |

**Auto-fix** (if user requests):
1. Show preview of changes (dry-run first)
2. Apply changes using Edit tool
3. Re-run affected test cases to verify

## Output Files

Write all outputs to the configured output directory (default: `tests/generated/`):

| File | Content |
|------|---------|
| `{name}.json` | Test suite definition |
| `{name}_report.json` | Raw test results with timing |
| `{name}_summary.md` | Markdown analysis report |
| `{name}_fixes.md` | Applied fixes changelog (if auto-fix ran) |

## Parameters

Parse from user's skill invocation (e.g., `/intent-test mode=analyze auto_fix=true`):

| Parameter | Default | Description |
|-----------|---------|-------------|
| `mode` | `analyze` | `generate` / `run` / `analyze` / `quick` / `fix` / `research` |
| `intents` | auto-detect | JSON string of custom intents |
| `name` | `intent_tests` | Suite name |
| `output_dir` | `tests/generated` | Output directory |
| `auto_fix` | `false` | Apply fixes automatically |
| `dry_run` | `true` | Preview fixes without applying |
| `max_fixes` | `5` | Maximum fixes to apply |
| `input` | — | Single input for `quick` mode |
| `regression_file` | — | Path to regression test cases |
| `performance_threshold` | — | Max response time in ms |

## Quick Mode

For `mode=quick`, skip generation. Directly:
1. Instantiate the engine
2. Run `engine.match(input)` on the provided input
3. Show: detected intent, confidence score, matched keywords/patterns
4. If confidence < 0.5, suggest the input may need LLM fallback

## Research Mode

For `mode=research`, produce a comprehensive report including:
- Coverage matrix (intent × test type)
- Failure root cause analysis with code references
- Prioritized improvement roadmap (short/mid/long term)
- Comparison with previous runs (if reports exist)

## Tips

- Always start with `analyze` mode for first-time testing
- Use `dry_run=true` before applying fixes to preview changes
- After fixes, re-run with regression file to verify
- For CI/CD: check `pass_rate` in report JSON, fail if < threshold
