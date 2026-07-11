"""Shared helper functions for the intent-test runner."""

import sys
import json
from pathlib import Path


# Global project root override — set by CLI --project-root flag
_project_root_override: Path = None


def set_project_root(path: str):
    """Set project root explicitly (called by CLI)."""
    global _project_root_override
    _project_root_override = Path(path).resolve()


def resolve_project_root() -> Path:
    """Return project root. Uses explicit override if set, otherwise auto-detect."""
    if _project_root_override:
        return _project_root_override

    current = Path(__file__).resolve().parent
    for _ in range(10):
        # Strong signals first (actual source code directories)
        if any([
            (current / "src").is_dir(),
            (current / "app").is_dir(),
            (current / "lib").is_dir(),
            (current / "pyproject.toml").exists(),
            (current / "setup.py").exists(),
            (current / "setup.cfg").exists(),
        ]):
            return current
        # Weak signals (could be sub-projects or tools)
        if any([
            (current / "requirements.txt").exists(),
            (current / "Pipfile").exists(),
            (current / "poetry.lock").exists(),
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
