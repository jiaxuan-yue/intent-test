"""Adapter classes for different intent recognition architectures."""

import sys
import time
import types
import asyncio
import inspect
import importlib
import importlib.util
from pathlib import Path
from typing import Optional, Callable, Dict, List, Any
from abc import ABC, abstractmethod

from common import resolve_project_root
from mock import mock_all_dependencies
from extract import extract_keywords_from_prompts


# ===================================================================
# Base Adapter
# ===================================================================

class BaseAdapter(ABC):
    """Unified interface for all intent recognition architectures."""

    @abstractmethod
    def detect_intents(self, input_text: str, context: dict = None) -> dict:
        """Run intent detection on input text."""

    @abstractmethod
    def get_keyword_sets(self) -> Dict[str, List[str]]:
        """Return keyword sets for automatic test generation."""

    @abstractmethod
    def get_all_functions(self) -> List[str]:
        """Return all testable function/method names."""

    @abstractmethod
    def get_handle_fn(self) -> Optional[Callable]:
        """Return the multi-turn handler if available."""

    def get_type(self) -> str:
        return self.__class__.__name__.replace("Adapter", "").lower()


# ===================================================================
# Dialog Functions Adapter
# ===================================================================

class DialogFunctionsAdapter(BaseAdapter):

    def __init__(self, project_root: Path = None):
        self.root = project_root or resolve_project_root()
        mock_all_dependencies()
        self.dialog = self._load_dialog()
        self.functions = self._discover_functions()
        self._keywords = extract_keywords_from_prompts(self.root)

    def _load_dialog(self):
        candidates = [
            self.root / "src" / "intent_recognition" / "dialog.py",
            self.root / "app" / "core" / "dialog.py",
            self.root / "src" / "dialog.py",
            self.root / "app" / "dialog.py",
            self.root / "dialog.py",
        ]
        dialog_path = None
        for c in candidates:
            if c.exists():
                dialog_path = c
                break
        if dialog_path is None:
            for p in self.root.rglob("dialog.py"):
                skip = any(d in str(p) for d in
                           ["test", "__pycache__", "node_modules", ".venv", "venv", "site-packages"])
                if not skip:
                    dialog_path = p
                    break
        if dialog_path is None:
            raise ImportError("dialog.py not found in project")

        src_dir = dialog_path.parent
        # Compute package path using relative_to from project root
        # e.g. project_root/app/core/task/dialog.py → "app.core.task"
        try:
            rel = dialog_path.parent.relative_to(self.root)
            pkg_parts = list(rel.parts)
        except ValueError:
            pkg_parts = []
        pkg_prefix = ".".join(pkg_parts) + "." if pkg_parts else ""

        # Register parent packages
        for i in range(len(pkg_parts)):
            pkg = ".".join(pkg_parts[:i + 1])
            if pkg not in sys.modules:
                pkg_mod = types.ModuleType(pkg)
                pkg_mod.__path__ = [str(self.root / Path(*pkg_parts[:i + 1]))]
                sys.modules[pkg] = pkg_mod

        for dep_name in ["prompts", "utils", "models", "config", "constants"]:
            dep_path = src_dir / f"{dep_name}.py"
            if dep_path.exists() and dep_name not in sys.modules:
                sys.path.insert(0, str(src_dir))
                try:
                    full_name = pkg_prefix + dep_name if pkg_prefix else dep_name
                    spec = importlib.util.spec_from_file_location(full_name, str(dep_path))
                    mod = importlib.util.module_from_spec(spec)
                    sys.modules[dep_name] = mod
                    if pkg_prefix:
                        sys.modules[full_name] = mod
                    spec.loader.exec_module(mod)
                except Exception:
                    pass

        spec = importlib.util.spec_from_file_location("dialog", str(dialog_path))
        dialog = importlib.util.module_from_spec(spec)
        sys.modules["dialog"] = dialog
        spec.loader.exec_module(dialog)
        return dialog

    def _discover_functions(self) -> Dict[str, Callable]:
        funcs = {}
        for name in dir(self.dialog):
            if name.startswith("__"):
                continue
            obj = getattr(self.dialog, name)
            if not callable(obj) or inspect.isclass(obj):
                continue
            if asyncio.iscoroutinefunction(obj):
                continue
            try:
                sig = inspect.signature(obj)
            except (ValueError, TypeError):
                continue
            params = list(sig.parameters.values())
            positional = [p for p in params
                          if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
                          and p.default is inspect.Parameter.empty]
            if len(positional) != 1:
                continue
            param = positional[0]
            if param.annotation is not inspect.Parameter.empty:
                ann = param.annotation
                if isinstance(ann, type) and ann is not str:
                    continue
                if hasattr(ann, "__origin__") and ann.__origin__ is not str:
                    continue
            ret = sig.return_annotation
            if ret is not inspect.Signature.empty:
                if isinstance(ret, type) and ret in (type(None), list, set):
                    continue
            name_lower = name.lower()
            if any(kw in name_lower for kw in
                   ["build", "format", "render", "generate_text",
                    "to_string", "serialize", "dump", "log"]):
                continue
            funcs[name] = obj
        if not funcs:
            raise ImportError("No single-argument detection functions found in dialog.py")
        return funcs

    def detect_intents(self, input_text: str, context: dict = None) -> dict:
        start = time.perf_counter()
        results = {}
        for func_name, func in self.functions.items():
            try:
                result = func(input_text)
                results[func_name] = {"result": result, "type": type(result).__name__}
            except Exception as e:
                results[func_name] = {"error": str(e)}
        elapsed = round((time.perf_counter() - start) * 1000, 2)
        return {"elapsed_ms": elapsed, "functions": results}

    def get_keyword_sets(self):
        return self._keywords

    def get_all_functions(self):
        return list(self.functions.keys())

    def get_handle_fn(self):
        return find_handler_by_signature(self.dialog)

    def get_module(self):
        return self.dialog


# ===================================================================
# RuleEngine Adapter
# ===================================================================

class RuleEngineAdapter(BaseAdapter):

    def __init__(self, project_root: Path = None):
        root = project_root or resolve_project_root()
        src = root / "src"
        if src.is_dir():
            sys.path.insert(0, str(src))
        from intent_recognition.engine import RuleEngine
        self.engine = RuleEngine()

    def detect_intents(self, input_text: str, context: dict = None) -> dict:
        start = time.perf_counter()
        result = self.engine.match(input_text)
        elapsed = round((time.perf_counter() - start) * 1000, 2)
        intent = getattr(result, "intent", None)
        if intent is not None and hasattr(intent, "value"):
            intent = intent.value
        return {
            "elapsed_ms": elapsed,
            "intent": intent or getattr(result, "detected_intent", None),
            "confidence": getattr(result, "confidence", 0.0),
            "details": {
                "matched_keywords": getattr(result, "matched_keywords", []),
                "matched_patterns": getattr(result, "matched_patterns", []),
            },
        }

    def get_keyword_sets(self):
        rules = getattr(self.engine, "keyword_rules", [])
        kw = {}
        for rule in rules:
            intent = str(getattr(rule, "intent", "unknown"))
            kw[intent] = list(getattr(rule, "keywords", []))
        return kw

    def get_all_functions(self):
        return ["match"]

    def get_handle_fn(self):
        return None


# ===================================================================
# LLM Analyzer Adapter
# ===================================================================

class LLMAnalyzerAdapter(BaseAdapter):

    def __init__(self, project_root: Path = None, adapter_path: str = None):
        if adapter_path:
            path = Path(adapter_path).resolve()
            spec = importlib.util.spec_from_file_location("llm_adapter", str(path))
            self._mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self._mod)
        else:
            raise ImportError("LLMAnalyzerAdapter requires --adapter-path")

    def detect_intents(self, input_text: str, context: dict = None) -> dict:
        start = time.perf_counter()
        result = self._mod.match(input_text)
        elapsed = round((time.perf_counter() - start) * 1000, 2)
        return {"elapsed_ms": elapsed, **(result if isinstance(result, dict) else {"raw": result})}

    def get_keyword_sets(self):
        return getattr(self._mod, "KEYWORDS", {})

    def get_all_functions(self):
        return ["match"]

    def get_handle_fn(self):
        return find_handler_by_signature(self._mod)


# ===================================================================
# Custom Adapter
# ===================================================================

class CustomAdapter(BaseAdapter):

    def __init__(self, adapter_path: str):
        path = Path(adapter_path).resolve()
        if not path.exists():
            raise ImportError(f"Adapter not found: {path}")
        spec = importlib.util.spec_from_file_location("custom_adapter", str(path))
        self._mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self._mod)
        if not hasattr(self._mod, "match"):
            raise ImportError("Custom adapter must define match(input_text: str) -> dict")

    def detect_intents(self, input_text: str, context: dict = None) -> dict:
        start = time.perf_counter()
        result = self._mod.match(input_text)
        elapsed = round((time.perf_counter() - start) * 1000, 2)
        return {"elapsed_ms": elapsed, **(result if isinstance(result, dict) else {"raw": result})}

    def get_keyword_sets(self):
        return getattr(self._mod, "KEYWORDS", {})

    def get_all_functions(self):
        return getattr(self._mod, "FUNCTIONS", ["match"])

    def get_handle_fn(self):
        return find_handler_by_signature(self._mod)


# ===================================================================
# Handler detection + adapter resolution
# ===================================================================

def find_handler_by_signature(module) -> Optional[Callable]:
    """Find a multi-turn handler by introspecting function signatures."""
    candidates = []
    for name in dir(module):
        if name.startswith("__"):
            continue
        obj = getattr(module, name)
        if not callable(obj) or inspect.isclass(obj):
            continue
        try:
            sig = inspect.signature(obj)
        except (ValueError, TypeError):
            continue
        params = list(sig.parameters.values())
        if len(params) < 2:
            continue
        msg_names = {"user_message", "message", "input_text", "text",
                     "msg", "input", "user_input", "query"}
        session_names = {"session", "plan_session", "context", "state",
                        "dialog_state", "current_session", "conversation"}
        has_msg = any(p.name.lower() in msg_names for p in params)
        has_session = any(p.name.lower() in session_names for p in params)
        if has_msg and has_session:
            score = len(params)
            if asyncio.iscoroutinefunction(obj):
                score += 100
            candidates.append((score, name, obj))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][2]


def resolve_adapter(adapter_type: str, project_root: Path = None,
                    adapter_path: str = None) -> Optional[BaseAdapter]:
    """Resolve the appropriate adapter."""
    if adapter_type == "dialog":
        return DialogFunctionsAdapter(project_root)
    elif adapter_type == "rule_engine":
        return RuleEngineAdapter(project_root)
    elif adapter_type == "llm_analyzer":
        return LLMAnalyzerAdapter(project_root, adapter_path)
    elif adapter_type == "custom":
        return CustomAdapter(adapter_path)
    elif adapter_type == "auto":
        for cls in [DialogFunctionsAdapter, RuleEngineAdapter]:
            try:
                return cls(project_root)
            except (ImportError, Exception):
                continue
        return None
    raise ValueError(f"Unknown adapter type: {adapter_type}")
