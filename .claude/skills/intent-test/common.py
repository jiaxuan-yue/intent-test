"""Shared helper functions for the intent-test runner."""

import sys
import json
from pathlib import Path


def resolve_project_root() -> Path:
    """Auto-detect project root by scanning for common markers."""
    current = Path(__file__).resolve().parent
    for _ in range(10):
        if any([
            (current / "src").is_dir(),
            (current / "app").is_dir(),
            (current / "lib").is_dir(),
            (current / "pyproject.toml").exists(),
            (current / "setup.py").exists(),
            (current / "setup.cfg").exists(),
            (current / "requirements.txt").exists(),
            (current / "Pipfile").exists(),
            (current / "poetry.lock").exists(),
            (current / ".git").is_dir(),
        ]):
            return current
        current = current.parent
    return Path.cwd()


def output(data: dict):
    """Print JSON output."""
    print(json.dumps(data, ensure_ascii=False, indent=2))


def exit_error(message: str):
    """Print error and exit with code 3."""
    print(json.dumps({"status": "error", "message": message}, ensure_ascii=False),
          file=sys.stderr)
    sys.exit(3)


def exit_with_code(pass_rate: float):
    """Exit with standardized code based on pass rate."""
    if pass_rate >= 1.0:
        sys.exit(0)
    elif pass_rate >= 0.5:
        sys.exit(1)
    else:
        sys.exit(2)
