"""Keyword extraction from project source files."""

import importlib.util
from pathlib import Path
from typing import Dict, List

from common import resolve_project_root


def extract_keywords_from_prompts(project_root: Path = None) -> Dict[str, List[str]]:
    """Extract keyword lists from prompts.py for test generation."""
    root = project_root or resolve_project_root()
    candidates = [
        root / "src" / "intent_recognition" / "prompts.py",
        root / "src" / "prompts.py",
        root / "prompts.py",
    ]
    for c in candidates:
        if c.exists():
            try:
                spec = importlib.util.spec_from_file_location("prompts_scan", str(c))
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                keywords = {}
                for name in dir(mod):
                    val = getattr(mod, name)
                    if isinstance(val, (list, dict)) and any(
                        kw in name.lower() for kw in
                        ["keyword", "intent", "trigger", "pattern", "time_", "yes_", "no_"]
                    ):
                        keywords[name] = val if isinstance(val, list) else list(val)
                if keywords:
                    return keywords
            except Exception:
                pass
    return {}
