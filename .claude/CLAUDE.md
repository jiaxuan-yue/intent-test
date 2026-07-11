# Intent Recognition Testing Project

This project contains an intent recognition testing skill and helper tools.

## Structure

```
.claude/
├── CLAUDE.md                       # This file
└── skills/
    ├── intent-test.md              # Skill definition (Claude instructions)
    └── intent-test/
        └── runner.py               # Helper script for test execution
```

## Dependencies

- Python 3.8+
- Pydantic 2.0+

## Quick Commands

```bash
# Run the skill
/intent-test

# Use the helper script directly
python .claude/skills/intent-test/runner.py generate --name my_tests
python .claude/skills/intent-test/runner.py run --suite tests/generated/my_tests.json
python .claude/skills/intent-test/runner.py quick --input "你好"
```

## Notes

- Test output goes to `tests/generated/` by default
- If the target project has no `src/intent_recognition/` module, provide intents via `--intents`
