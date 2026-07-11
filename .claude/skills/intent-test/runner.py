#!/usr/bin/env python3
"""
Intent Recognition Test Runner — execution layer only.

This script ONLY executes tests. Understanding the codebase and generating
tests/configs is Claude's job (the intelligence layer).

Architecture:
    Claude (intelligence)           runner.py (execution)
    ┌─────────────────────┐        ┌──────────────────────┐
    │ Read code           │───────▶│ config.json          │
    │ Understand arch     │        │ test scenarios       │
    │ Generate config     │        │ mock strategies      │
    │ Analyze failures    │◀───────│ test reports         │
    └─────────────────────┘        └──────────────────────┘

Layered testing:
    Layer 1 (routing)    → run_layer1    (LLM routing with mock)
    Layer 2 (FSM)        → run_multi     (state machine transitions)
    Layer 3 (functions)  → run           (single-function accuracy)
    Unified report       → report_unified (combine all layers)

Commands:
    generate / run / quick / report       Single-turn testing
    generate_multi / run_multi / report_multi   Multi-turn FSM testing
    run_layer1                            LLM routing layer testing
    report_unified                        Combined layered report
    check_deps / fsm_coverage             Diagnostics

Exit codes: 0=pass | 1=partial(≥50%) | 2=fail(<50%) | 3=error
"""

import sys
import json
import time
import types
import asyncio
import argparse
import inspect
import importlib
import importlib.util
from pathlib import Path
from typing import Optional, Callable, Dict, Any, List
from abc import ABC, abstractmethod


# ===================================================================
# Adapter Base Class
# ===================================================================

class BaseAdapter(ABC):
    """Unified interface for all intent recognition architectures."""

    @abstractmethod
    def detect_intents(self, input_text: str, context: dict = None) -> dict:
        """Run intent detection on input text.

        Returns:
            dict with keys:
                functions: {func_name: {"result": ..., "type": ...}}  (dialog)
                intent: str|None                                       (engine)
                confidence: float                                      (engine)
                details: {...}
                elapsed_ms: float
        """

    @abstractmethod
    def get_keyword_sets(self) -> Dict[str, List[str]]:
        """Return keyword sets for automatic test generation.

        Returns: {source_name: [keyword1, keyword2, ...]}
        """

    @abstractmethod
    def get_all_functions(self) -> List[str]:
        """Return all testable function/method names (for quick mode)."""

    @abstractmethod
    def get_handle_fn(self) -> Optional[Callable]:
        """Return the multi-turn handler if available."""

    def get_type(self) -> str:
        return self.__class__.__name__.replace("Adapter", "").lower()


# ===================================================================
# Dialog Functions Adapter (function-based projects)
# ===================================================================

class DialogFunctionsAdapter(BaseAdapter):

    def __init__(self, project_root: Path = None):
        self.root = project_root or _resolve_project_root()
        _mock_all_dependencies()
        self.dialog = self._load_dialog()
        self.functions = self._discover_functions()
        self._keywords = _extract_keywords_from_prompts(self.root)

    def _load_dialog(self):
        """Find dialog.py by searching common locations, then fall back to rglob."""
        # Try common locations first (fast path)
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

        # Fall back to recursive search
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
        # Compute full package path from dialog.py location
        # e.g. app/core/task/dialog.py → pkg_prefix = "app.core.task."
        pkg_parts = []
        p = src_dir
        while p.parent != p:
            if (p / "__init__.py").exists():
                pkg_parts.insert(0, p.name)
                p = p.parent
            else:
                break
        pkg_prefix = ".".join(pkg_parts) + "." if pkg_parts else ""

        # Register parent packages (e.g. app, app.core, app.core.task)
        for i in range(len(pkg_parts)):
            pkg = ".".join(pkg_parts[:i + 1])
            if pkg not in sys.modules:
                pkg_mod = types.ModuleType(pkg)
                pkg_mod.__path__ = [str(p)]
                sys.modules[pkg] = pkg_mod

        for dep_name in ["prompts", "utils", "models", "config", "constants"]:
            dep_path = src_dir / f"{dep_name}.py"
            if dep_path.exists() and dep_name not in sys.modules:
                sys.path.insert(0, str(src_dir))
                try:
                    # Register with both simple name and full package path
                    full_name = pkg_prefix + dep_name if pkg_prefix else dep_name
                    spec = importlib.util.spec_from_file_location(full_name, str(dep_path))
                    mod = importlib.util.module_from_spec(spec)
                    sys.modules[dep_name] = mod          # simple: "prompts"
                    if pkg_prefix:
                        sys.modules[full_name] = mod     # full: "app.core.task.prompts"
                    spec.loader.exec_module(mod)
                except Exception:
                    pass

        spec = importlib.util.spec_from_file_location("dialog", str(dialog_path))
        dialog = importlib.util.module_from_spec(spec)
        sys.modules["dialog"] = dialog
        spec.loader.exec_module(dialog)
        return dialog

    def _discover_functions(self) -> Dict[str, Callable]:
        """Auto-discover intent detection functions using deep introspection.

        Classification strategy — a function is an "intent detector" if:
        1. Takes exactly 1 positional str parameter (the input text)
        2. Returns bool, str, dict, or enum (not None, not complex objects)
        3. Is NOT a builder/formatter/renderer (returns text/html/etc.)

        A function is classified as "helper" (excluded) if:
        - Takes >1 required positional args (it's a utility, not a detector)
        - Returns a formatted string (it's a text builder)
        - Takes non-str first param (it's a data processor)
        - Is a class constructor or decorator
        """
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

            # Rule 1: exactly 1 required positional arg
            positional = [p for p in params
                          if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
                          and p.default is inspect.Parameter.empty]
            if len(positional) != 1:
                continue

            # Rule 2: first param must be str or untyped
            param = positional[0]
            if param.annotation is not inspect.Parameter.empty:
                ann = param.annotation
                if isinstance(ann, type) and ann is not str:
                    continue
                if hasattr(ann, "__origin__") and ann.__origin__ is not str:
                    continue

            # Rule 3: check return type annotation — exclude text builders
            ret = sig.return_annotation
            if ret is not inspect.Signature.empty:
                if isinstance(ret, type) and ret in (type(None), list, set):
                    continue  # returns None, list, or set — likely a helper

            # Rule 4: name-based soft filter (not exclusion, just classification)
            name_lower = name.lower()
            builder_keywords = ["build", "format", "render", "generate_text",
                                "to_string", "serialize", "dump", "log"]
            if any(kw in name_lower for kw in builder_keywords):
                continue  # likely a text builder, not a detector

            funcs[name] = obj

        if not funcs:
            raise ImportError("No single-argument detection functions found in dialog.py")
        return funcs

    def detect_intents(self, input_text: str, context: dict = None) -> dict:
        start = time.perf_counter()
        results = {}
        for func_name, func in self.functions.items():
            try:
                # Skip async handlers for single-turn (they are multi-turn handlers)
                if "handle" in func_name.lower() and asyncio.iscoroutinefunction(func):
                    continue
                result = func(input_text)
                results[func_name] = {"result": result, "type": type(result).__name__}
            except Exception as e:
                results[func_name] = {"error": str(e)}
        elapsed = round((time.perf_counter() - start) * 1000, 2)
        return {"elapsed_ms": elapsed, "functions": results}

    def get_keyword_sets(self) -> Dict[str, List[str]]:
        return self._keywords

    def get_all_functions(self) -> List[str]:
        return list(self.functions.keys())

    def get_handle_fn(self) -> Optional[Callable]:
        """Auto-detect multi-turn handler by signature."""
        return _find_handler_by_signature(self.dialog)

    def get_module(self):
        return self.dialog


# ===================================================================
# RuleEngine Adapter (classic class-based)
# ===================================================================

class RuleEngineAdapter(BaseAdapter):

    def __init__(self, project_root: Path = None):
        root = project_root or _resolve_project_root()
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

    def get_keyword_sets(self) -> Dict[str, List[str]]:
        rules = getattr(self.engine, "keyword_rules", [])
        kw = {}
        for rule in rules:
            intent = str(getattr(rule, "intent", "unknown"))
            kw[intent] = list(getattr(rule, "keywords", []))
        return kw

    def get_all_functions(self) -> List[str]:
        return ["match"]

    def get_handle_fn(self) -> Optional[Callable]:
        return None


# ===================================================================
# LLM Analyzer Adapter (structured output, ExecutionPlan, etc.)
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
        return _find_handler_by_signature(self._mod)


# ===================================================================
# Custom Adapter (user-provided adapter.py)
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
        return _find_handler_by_signature(self._mod)


# ===================================================================
# Shared: signature-based handler detection
# ===================================================================

def _find_handler_by_signature(module) -> Optional[Callable]:
    """Find a multi-turn handler in any module by introspecting function signatures.

    Looks for async functions with message-like + session-like parameters.
    No hardcoded function names — pure signature-based detection.
    """
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

        # Check for message-like + session-like params by name
        msg_names = {"user_message", "message", "input_text", "text",
                     "msg", "input", "user_input", "query"}
        session_names = {"session", "plan_session", "context", "state",
                        "dialog_state", "current_session", "conversation"}

        has_msg = any(p.name.lower() in msg_names for p in params)
        has_session = any(p.name.lower() in session_names for p in params)

        if has_msg and has_session:
            # Prefer async handlers for multi-turn
            score = len(params)
            if asyncio.iscoroutinefunction(obj):
                score += 100
            candidates.append((score, name, obj))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][2]


# ===================================================================
# Adapter Resolution
# ===================================================================

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


# ===================================================================
# Dependency Mocking
# ===================================================================

# Comprehensive mock list covering mainstream AI/LLM frameworks
MOCK_MODULES = {
    # LangChain
    "langchain_core": ["messages", "prompts", "language_models", "output_parsers", "callbacks"],
    "langchain_core.messages": ["HumanMessage", "AIMessage", "SystemMessage", "ToolMessage"],
    "langchain_core.prompts": ["ChatPromptTemplate", "PromptTemplate", "MessagesPlaceholder"],
    "langchain_core.language_models": ["BaseChatModel", "BaseLanguageModel"],
    "langchain_core.output_parsers": ["JsonOutputParser", "StrOutputParser", "PydanticOutputParser"],
    "langchain_core.callbacks": ["BaseCallbackHandler"],
    "langchain_core.runnables": ["RunnablePassthrough", "RunnableLambda"],
    # LangGraph
    "langgraph": ["graph", "prebuilt", "checkpoint"],
    "langgraph.graph": ["StateGraph", "MessageGraph", "END", "START"],
    "langgraph.graph.message": ["add_messages"],
    "langgraph.prebuilt": ["ToolNode", "create_react_agent"],
    "langgraph.checkpoint": ["MemorySaver"],
    # LLM providers
    "langchain_openai": ["ChatOpenAI", "OpenAIEmbeddings"],
    "langchain_deepseek": ["ChatDeepSeek"],
    "langchain_anthropic": ["ChatAnthropic"],
    "langchain_community": ["chat_models", "llms", "embeddings"],
    "openai": ["OpenAI", "AsyncOpenAI"],
    "anthropic": ["Anthropic", "AsyncAnthropic"],
    # Other common
    "pydantic": ["BaseModel", "Field"],
    "pydantic.v1": ["BaseModel", "Field"],
}


def _mock_all_dependencies():
    """Mock heavy AI framework dependencies so project modules can be imported."""
    for mod_name, attrs in MOCK_MODULES.items():
        if mod_name not in sys.modules:
            mock = types.ModuleType(mod_name)
            for attr in attrs:
                mock_cls = type(attr, (), {
                    "__init__": lambda self, *a, **kw: None,
                    "__call__": lambda self, *a, **kw: None,
                    "content": "",
                    "model_fields": {},
                })
                setattr(mock, attr, mock_cls)
            sys.modules[mod_name] = mock

    # Mock project-specific generator modules so async LLM calls trigger fallback.
    # These are common patterns in intent recognition projects that use LLM layers.
    # The actual module path varies per project — mock the most common patterns.
    for gen_mod in [
        "app", "app.core", "app.core.task_plan",
        "app.core.task_plan.generator",
        "app.core.intent", "app.core.intent.generator",
        "app.services", "app.services.llm",
        "core", "core.generator", "core.llm",
    ]:
        if gen_mod not in sys.modules:
            sys.modules[gen_mod] = types.ModuleType(gen_mod)

    mock_gen = sys.modules["app.core.task_plan.generator"]
    if not hasattr(mock_gen, "_get_chat_model"):
        def _mock_get_chat_model():
            raise ImportError("Mocked — triggers fallback to default questions")
        mock_gen._get_chat_model = _mock_get_chat_model


def check_dependencies(project_root: Path = None) -> Dict:
    """Check which dependencies are available vs mocked vs missing."""
    root = project_root or _resolve_project_root()
    results = {"available": [], "mocked": [], "missing": []}

    # Check real imports
    for mod_name in sorted(MOCK_MODULES.keys()):
        try:
            # Temporarily remove mock to check real availability
            saved = sys.modules.pop(mod_name, None)
            importlib.import_module(mod_name)
            results["available"].append(mod_name)
        except ImportError:
            if mod_name in sys.modules:
                results["mocked"].append(mod_name)
            else:
                results["missing"].append(mod_name)
        finally:
            if saved is not None:
                sys.modules[mod_name] = saved

    # Check project modules
    src = root / "src"
    if src.is_dir():
        sys.path.insert(0, str(src))

    project_mods = ["intent_recognition", "dialog", "prompts", "models"]
    for mod in project_mods:
        try:
            importlib.import_module(mod)
            results["available"].append(f"project:{mod}")
        except ImportError:
            results["missing"].append(f"project:{mod}")

    return results


# ===================================================================
# Keyword Extraction
# ===================================================================

def _extract_keywords_from_prompts(project_root: Path = None) -> Dict[str, List[str]]:
    """Extract keyword lists from prompts.py for test generation."""
    root = project_root or _resolve_project_root()
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


# ===================================================================
# Commands: Single-turn
# ===================================================================

def cmd_generate(args):
    """Generate single-turn test cases."""
    try:
        adapter = resolve_adapter(args.adapter, adapter_path=args.adapter_path)
    except Exception as e:
        _exit_error(str(e))

    if adapter is None:
        _exit_error("No intent system found. Use --adapter custom --adapter-path adapter.py")

    test_cases = []
    case_id = 0
    keywords = adapter.get_keyword_sets()
    func_names = adapter.get_all_functions()

    if isinstance(adapter, DialogFunctionsAdapter):
        for func_name in func_names:
            if "handle" in func_name.lower():
                continue  # Skip async handlers for single-turn

            # Positive from keywords
            for kw_source, kw_list in keywords.items():
                if func_name.lower().replace("_", "") in kw_source.lower().replace("_", ""):
                    for kw in (kw_list if isinstance(kw_list, list) else list(kw_list))[:5]:
                        case_id += 1
                        test_cases.append({
                            "id": f"pos_{func_name}_{case_id}",
                            "input": kw,
                            "expected_function": func_name,
                            "expected_result": True,
                            "case_type": "positive",
                            "priority": "p0",
                        })

            # Adversarial
            for adv_input, label in [
                ("", "empty"), ("不想学", "negation"),
                ("abc" * 100, "long_text"), ("🎉🎊", "emoji"),
                ("   ", "whitespace"), ("今天天气不错", "off_topic"),
                ("周末去看电影", "time_false_positive"),
                ("不计划了", "negation_update"),
            ]:
                case_id += 1
                test_cases.append({
                    "id": f"adv_{func_name}_{case_id}",
                    "input": adv_input,
                    "expected_function": func_name,
                    "expected_result": False,
                    "case_type": "adversarial",
                    "priority": "p1",
                    "label": label,
                })

            # Boundary
            for b_input, label in [
                ("是的我要退出", "yes_exit_conflict"),
                ("不想学但是想看计划", "negation_plan"),
                ("好的我不想", "yes_no_mixed"),
            ]:
                case_id += 1
                test_cases.append({
                    "id": f"bnd_{func_name}_{case_id}",
                    "input": b_input,
                    "expected_function": func_name,
                    "case_type": "boundary",
                    "priority": "p1",
                    "label": label,
                })

    elif isinstance(adapter, RuleEngineAdapter):
        for intent, kws in keywords.items():
            for kw in kws[:3]:
                case_id += 1
                test_cases.append({
                    "id": f"pos_{intent}_{case_id}",
                    "input": kw,
                    "expected_intent": intent,
                    "case_type": "positive",
                    "priority": "p0",
                })

    suite = {"name": args.name, "adapter_type": adapter.get_type(), "test_cases": test_cases}
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    filepath = output_dir / f"{args.name}.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(suite, f, ensure_ascii=False, indent=2)

    _output({"status": "ok", "test_count": len(test_cases),
             "adapter_type": adapter.get_type(), "path": str(filepath)})


def cmd_run(args):
    """Run single-turn test suite."""
    try:
        adapter = resolve_adapter(args.adapter, adapter_path=args.adapter_path)
    except Exception as e:
        _exit_error(str(e))

    if adapter is None:
        _exit_error("No intent system found")

    suite_path = Path(args.suite)
    if not suite_path.exists():
        _exit_error(f"Suite not found: {suite_path}")

    with open(suite_path, "r", encoding="utf-8") as f:
        suite = json.load(f)

    results = []
    passed = failed = errors = 0

    for tc in suite.get("test_cases", []):
        try:
            result = adapter.detect_intents(tc["input"])
            atype = adapter.get_type()

            if atype == "dialogfunctions":
                func_name = tc.get("expected_function")
                expected = tc.get("expected_result")
                if func_name and func_name in result.get("functions", {}):
                    actual = result["functions"][func_name].get("result")
                    is_pass = actual == expected
                else:
                    is_pass = True
                results.append({
                    "id": tc["id"], "input": tc["input"],
                    "expected_function": func_name, "expected_result": expected,
                    "actual": result.get("functions", {}).get(func_name, {}),
                    "elapsed_ms": result["elapsed_ms"], "passed": is_pass,
                    "case_type": tc.get("case_type"),
                })
            else:
                actual = result.get("intent")
                expected = tc.get("expected_intent")
                is_pass = actual == expected
                results.append({
                    "id": tc["id"], "input": tc["input"],
                    "expected": expected, "actual": actual,
                    "confidence": result.get("confidence", 0.0),
                    "elapsed_ms": result["elapsed_ms"], "passed": is_pass,
                    "case_type": tc.get("case_type"),
                })

            if is_pass:
                passed += 1
            else:
                failed += 1
        except Exception as e:
            errors += 1
            results.append({"id": tc["id"], "input": tc["input"],
                            "error": str(e), "passed": False})

    total = len(results)
    pass_rate = round(passed / total, 4) if total > 0 else 0
    report = {
        "suite_name": suite.get("name", "unknown"),
        "adapter_type": adapter.get_type(),
        "total": total, "passed": passed, "failed": failed,
        "errors": errors, "pass_rate": pass_rate, "results": results,
    }

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

    _output({"status": "ok", "total": total, "passed": passed,
             "failed": failed, "errors": errors, "pass_rate": pass_rate,
             "report_path": str(args.output) if args.output else None})
    _exit_with_code(pass_rate)


def cmd_quick(args):
    """Quick single-input test."""
    try:
        adapter = resolve_adapter(args.adapter, adapter_path=args.adapter_path)
    except Exception as e:
        _exit_error(str(e))

    context = None
    if args.context:
        try:
            context = json.loads(args.context)
        except json.JSONDecodeError:
            _exit_error(f"Invalid JSON for --context: {args.context}")

    result = adapter.detect_intents(args.input, context)
    output = {
        "input": args.input,
        "context": context,
        "functions": adapter.get_all_functions(),
        **result,
    }
    _output(output)


def cmd_report(args):
    """Display readable single-turn report."""
    report_path = Path(args.results)
    if not report_path.exists():
        _exit_error(f"Report not found: {report_path}")

    with open(report_path, "r", encoding="utf-8") as f:
        report = json.load(f)

    print(f"\n{'='*60}")
    print(f"  Intent Test Report: {report.get('suite_name', 'unknown')}")
    print(f"  Adapter: {report.get('adapter_type', 'unknown')}")
    print(f"{'='*60}")
    print(f"  Total: {report['total']}")
    print(f"  ✅ Passed: {report['passed']} ({report['pass_rate']*100:.1f}%)")
    print(f"  ❌ Failed: {report['failed']}")
    print(f"  ⚠️  Errors: {report['errors']}")

    failures = [r for r in report.get("results", []) if not r["passed"]]
    if failures:
        print(f"\n  Failed cases:")
        for r in failures[:15]:
            print(f"    ❌ {r['id']}: \"{r['input'][:40]}\"")
            if "expected" in r:
                print(f"       expected={r['expected']}, actual={r['actual']}")
            if "error" in r:
                print(f"       error: {r['error']}")
    print(f"\n{'='*60}\n")


def cmd_template(args):
    """Generate adapter template."""
    template = '''"""
Intent Recognition Adapter — edit match() to call your system.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

def match(input_text: str) -> dict:
    """Returns: {"intent": str|None, "confidence": float, "details": {...}}"""
    raise NotImplementedError("Edit this adapter for your project.")

KEYWORDS = {}  # Optional: keyword sets for test generation
FUNCTIONS = ["match"]  # Optional: list of testable functions
'''
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(template)
    _output({"status": "ok", "path": str(out)})


# ===================================================================
# Config-driven architecture (Claude generates this)
# ===================================================================

def _load_config(config_path: str = None) -> Dict:
    """Load architecture config generated by Claude.

    Config format:
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
      "states": ["idle", "collecting", "await_offer", ...]
    }
    """
    if config_path is None:
        # Auto-detect config file
        for candidate in [
            Path(".claude/skills/intent-test/config.json"),
            Path("tests/generated/config.json"),
            Path("intent_test_config.json"),
        ]:
            if candidate.exists():
                config_path = str(candidate)
                break

    if config_path is None:
        return {}

    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _apply_llm_mocks(config: Dict):
    """Apply LLM mock strategies from config.

    For each function listed in llm_mocks, monkey-patch it to use
    a keyword-based fallback instead of calling the LLM.
    """
    for layer_name, layer in config.get("layers", {}).items():
        mocks = layer.get("llm_mocks", {})
        if not mocks:
            continue

        # Find the module containing these functions
        for func_name, strategy in mocks.items():
            # Search loaded modules for this function
            for mod_name, mod in list(sys.modules.items()):
                if hasattr(mod, func_name):
                    original = getattr(mod, func_name)
                    if strategy == "keyword_fallback":
                        # Replace with a sync keyword-based version
                        def make_fallback(fn_name, orig):
                            def fallback(*args, **kwargs):
                                # If original is async, return a coroutine
                                if asyncio.iscoroutinefunction(orig):
                                    async def _async_fallback():
                                        return _keyword_fallback_for(fn_name, args, kwargs)
                                    return _async_fallback()
                                return _keyword_fallback_for(fn_name, args, kwargs)
                            return fallback
                        setattr(mod, func_name, make_fallback(func_name, original))
                    elif strategy == "return_true":
                        if asyncio.iscoroutinefunction(original):
                            async def _true():
                                return True
                            setattr(mod, func_name, _true)
                        else:
                            setattr(mod, func_name, lambda *a, **kw: True)
                    elif strategy == "return_false":
                        if asyncio.iscoroutinefunction(original):
                            async def _false():
                                return False
                            setattr(mod, func_name, _false)
                        else:
                            setattr(mod, func_name, lambda *a, **kw: False)
                    elif strategy.startswith("return:"):
                        val = json.loads(strategy.split(":", 1)[1])
                        if asyncio.iscoroutinefunction(original):
                            async def _val():
                                return val
                            setattr(mod, func_name, _val)
                        else:
                            setattr(mod, func_name, lambda *a, **kw: val)


def _keyword_fallback_for(func_name: str, args, kwargs) -> Any:
    """Generic keyword-based fallback for mocked LLM functions.

    Maps common LLM function patterns to keyword checks.
    """
    # Extract input text from args
    input_text = ""
    for arg in args:
        if isinstance(arg, str):
            input_text = arg
            break
        elif isinstance(arg, dict):
            input_text = arg.get("content", arg.get("text", arg.get("input", "")))
            break

    name_lower = func_name.lower()

    # Pattern-based fallback
    if "plan_related" in name_lower or "plan_intent" in name_lower:
        plan_kw = ["计划", "学习", "plan", "learn", "安排", "制定"]
        return any(kw in input_text for kw in plan_kw)
    elif "exit" in name_lower or "quit" in name_lower or "stop" in name_lower:
        exit_kw = ["退出", "取消", "exit", "quit", "stop", "不要", "算了"]
        return any(kw in input_text for kw in exit_kw)
    elif "yes" in name_lower or "confirm" in name_lower or "accept" in name_lower:
        yes_kw = ["好的", "是的", "确认", "yes", "ok", "对"]
        return any(kw in input_text for kw in yes_kw)
    elif "should_generate" in name_lower or "enough_info" in name_lower:
        return len(input_text) > 5  # assume enough info if input is long enough
    else:
        return False  # safe default for unknown functions


# ===================================================================
# Layer 1 testing (LLM routing layer)
# ===================================================================

def cmd_run_layer1(args):
    """Test the LLM routing layer with mock strategies.

    This tests Layer 1 (analyzer/routing) by:
    1. Loading config to find the routing entry point
    2. Applying LLM mocks per config
    3. Running test inputs through the routing function
    4. Checking which intents/flags the router outputs
    """
    config = _load_config(args.config)
    if not config:
        _exit_error("No config found. Run /intent-test first so Claude generates config.json")

    _apply_llm_mocks(config)

    # Find routing layer
    routing = config.get("layers", {}).get("routing", {})
    entry_path = routing.get("entry", "")
    params = routing.get("params", {})

    if not entry_path:
        _exit_error("No routing entry defined in config")

    # Import the routing function
    module_path, func_name = entry_path.rsplit(".", 1)
    try:
        mod = importlib.import_module(module_path)
        route_fn = getattr(mod, func_name)
    except (ImportError, AttributeError) as e:
        _exit_error(f"Cannot import routing: {e}")

    # Load test suite
    suite_path = Path(args.suite)
    if not suite_path.exists():
        _exit_error(f"Suite not found: {suite_path}")

    with open(suite_path, "r", encoding="utf-8") as f:
        suite = json.load(f)

    results = []
    passed = failed = errors = 0

    for tc in suite.get("test_cases", []):
        try:
            start = time.perf_counter()

            # Build kwargs from config params + test input
            call_kwargs = dict(params)
            call_kwargs["user_message"] = tc["input"]
            call_kwargs["message"] = tc["input"]

            if asyncio.iscoroutinefunction(route_fn):
                result = asyncio.run(route_fn(**call_kwargs))
            else:
                result = route_fn(**call_kwargs)

            elapsed = round((time.perf_counter() - start) * 1000, 2)

            # Extract routing decisions
            if isinstance(result, dict):
                decisions = result
            elif hasattr(result, "__dict__"):
                decisions = {k: v for k, v in result.__dict__.items()
                            if not k.startswith("_")}
            else:
                decisions = {"result": result}

            # Check expectations
            expect = tc.get("expect_routing", {})
            is_pass = True
            failures = []
            for key, expected in expect.items():
                actual = decisions.get(key)
                if actual != expected:
                    is_pass = False
                    failures.append(f"{key}: expected {expected!r}, got {actual!r}")

            results.append({
                "id": tc["id"],
                "input": tc["input"],
                "decisions": decisions,
                "elapsed_ms": elapsed,
                "passed": is_pass,
                "failures": failures,
            })

            if is_pass:
                passed += 1
            else:
                failed += 1

        except Exception as e:
            errors += 1
            results.append({
                "id": tc["id"], "input": tc["input"],
                "error": str(e), "passed": False,
            })

    total = len(results)
    pass_rate = round(passed / total, 4) if total else 0
    report = {
        "layer": "routing",
        "total": total, "passed": passed, "failed": failed,
        "errors": errors, "pass_rate": pass_rate,
        "results": results,
    }

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

    _output({"status": "ok", "layer": "routing",
             "total": total, "passed": passed, "failed": failed,
             "pass_rate": pass_rate,
             "report_path": str(args.output) if args.output else None})
    _exit_with_code(pass_rate)


# ===================================================================
# Commands: Multi-turn FSM
# ===================================================================

_BUILTIN_SCENARIOS = [
    # --- Happy paths ---
    {
        "name": "happy_path_init",
        "description": "New session: idle → active → confirmation",
        "initial_state": {"has_context": False, "session": None},
        "turns": [
            {"input": "我想开始", "expect": {"status": "active", "mode": "init", "handled": True}},
            {"input": "每天一次", "expect": {"status": "active", "handled": True}},
        ],
    },
    {
        "name": "happy_path_update",
        "description": "Update existing: idle → active(update) → confirmation",
        "initial_state": {"has_context": True, "session": None},
        "turns": [
            {"input": "我想修改", "expect": {"status": "active", "mode": "update", "handled": True}},
            {"input": "改成两次", "expect": {"status": "active", "handled": True}},
        ],
    },
    # --- Exit flows ---
    {
        "name": "exit_confirmed",
        "description": "User exits during active session → confirmed → idle",
        "initial_state": {"has_context": False, "session": None},
        "turns": [
            {"input": "我想开始", "expect": {"status": "active", "handled": True}},
            {"input": "算了退出", "expect": {"status": "confirm_exit", "handled": True}},
            {"input": "是的退出", "expect": {"status": "idle", "handled": True}},
        ],
    },
    {
        "name": "exit_cancelled",
        "description": "User exits then cancels → resume active",
        "initial_state": {"has_context": False, "session": None},
        "turns": [
            {"input": "我想开始", "expect": {"status": "active", "handled": True}},
            {"input": "算了退出", "expect": {"status": "confirm_exit", "handled": True}},
            {"input": "不继续", "expect": {"status": "active", "handled": True}},
        ],
    },
    # --- Offer flows ---
    {
        "name": "offer_accepted",
        "description": "System offers action, user accepts → active",
        "initial_state": {"has_context": False, "session": None},
        "turns": [
            {"input": "帮我安排", "expect": {"handled": True}},
        ],
    },
    {
        "name": "offer_rejected",
        "description": "Off-topic input → not handled",
        "initial_state": {"has_context": False, "session": None},
        "turns": [
            {"input": "今天天气怎样", "expect": {"handled": False}},
        ],
    },
    # --- Confirmation flows ---
    {
        "name": "confirm_accepted",
        "description": "Proposal accepted → active",
        "initial_state": {"has_context": False, "session": None},
        "turns": [
            {"input": "我想开始", "expect": {"status": "active", "handled": True}},
        ],
    },
    {
        "name": "confirm_rejected",
        "description": "User declines → idle",
        "initial_state": {"has_context": False, "session": None},
        "turns": [
            {"input": "算了不要了", "expect": {"handled": True}},
        ],
    },
    # --- Update after confirm ---
    {
        "name": "post_confirm_update",
        "description": "After confirmation, user wants to update → active(update)",
        "initial_state": {"has_context": True, "session": None},
        "turns": [
            {"input": "修改我的设置", "expect": {"status": "active", "mode": "update", "handled": True}},
        ],
    },
    # --- Guard rails ---
    {
        "name": "detail_during_active_no_exit",
        "description": "User provides details during active — should NOT trigger exit",
        "initial_state": {"has_context": False, "session": None},
        "turns": [
            {"input": "我想开始", "expect": {"status": "active", "handled": True}},
            {"input": "每天下午3点一次", "expect": {"status": "active", "handled": True}},
        ],
    },
    # --- Fallback ---
    {
        "name": "update_without_context",
        "description": "Update keyword but no existing context → falls back to init",
        "initial_state": {"has_context": False, "session": None},
        "turns": [
            {"input": "修改设置", "expect": {"status": "active", "handled": True}},
        ],
    },
    {
        "name": "max_turns_auto_complete",
        "description": "Active session reaches max turns → auto-generates result",
        "initial_state": {"has_context": False, "session": None},
        "turns": [
            {"input": "我想开始", "expect": {"status": "active", "handled": True}},
            {"input": "每天", "expect": {"status": "active", "handled": True}},
            {"input": "一次", "expect": {"handled": True}},
        ],
    },
    # --- Regression patterns (universal, not project-specific) ---
    {
        "name": "regression_broad_keyword_false_positive",
        "description": "Regression: overly broad keywords match unrelated inputs",
        "initial_state": {"has_context": False, "session": None},
        "turns": [
            {"input": "随便聊聊", "expect": {"handled": False}},
            {"input": "讲个笑话", "expect": {"handled": False}},
            {"input": "你是谁", "expect": {"handled": False}},
        ],
    },
    {
        "name": "regression_negation_not_checked",
        "description": "Regression: negation prefix not handled before keyword match",
        "initial_state": {"has_context": False, "session": None},
        "turns": [
            {"input": "不想开始", "expect": {"handled": False}},
            {"input": "不要修改", "expect": {"handled": False}},
        ],
    },
    # --- State injection: start from non-idle states ---
    {
        "name": "inject_from_confirm_exit_yes",
        "description": "Start from confirm_exit state → user confirms → idle",
        "initial_state": {
            "has_context": False,
            "session": {"status": "confirm_exit", "mode": "", "turns": 0, "messages": []},
        },
        "turns": [
            {"input": "是的退出", "expect": {"status": "idle", "handled": True}},
        ],
    },
    {
        "name": "inject_from_confirm_exit_no",
        "description": "Start from confirm_exit state → user cancels → active",
        "initial_state": {
            "has_context": False,
            "session": {"status": "confirm_exit", "mode": "", "turns": 0, "messages": []},
        },
        "turns": [
            {"input": "不继续", "expect": {"status": "active", "handled": True}},
        ],
    },
    {
        "name": "inject_from_await_offer_accept",
        "description": "Start from await_offer state → user accepts → active",
        "initial_state": {
            "has_context": False,
            "session": {"status": "await_offer", "mode": "", "turns": 0, "messages": []},
        },
        "turns": [
            {"input": "好的开始", "expect": {"status": "active", "handled": True}},
        ],
    },
    {
        "name": "inject_from_await_offer_reject",
        "description": "Start from await_offer state → user rejects → idle",
        "initial_state": {
            "has_context": False,
            "session": {"status": "await_offer", "mode": "", "turns": 0, "messages": []},
        },
        "turns": [
            {"input": "不要了", "expect": {"status": "idle", "handled": True}},
        ],
    },
    {
        "name": "inject_from_active_mid_conversation",
        "description": "Start from active with 2 turns already → continue collecting",
        "initial_state": {
            "has_context": False,
            "session": {"status": "active", "mode": "init", "turns": 2, "messages": ["我想开始", "每天一次"]},
        },
        "turns": [
            {"input": "下午3点", "expect": {"status": "active", "handled": True}},
        ],
    },
    {
        "name": "inject_from_await_confirm_accept",
        "description": "Start from await_confirm → user accepts → active",
        "initial_state": {
            "has_context": False,
            "session": {"status": "await_confirm", "mode": "", "turns": 0, "messages": []},
        },
        "turns": [
            {"input": "可以", "expect": {"status": "active", "handled": True}},
        ],
    },
]


def cmd_generate_multi(args):
    """Generate multi-turn FSM test scenarios."""
    suite = {"name": args.name, "type": "multi_turn", "scenarios": _BUILTIN_SCENARIOS}
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    filepath = output_dir / f"{args.name}_multi.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(suite, f, ensure_ascii=False, indent=2)
    _output({"status": "ok", "scenario_count": len(_BUILTIN_SCENARIOS), "path": str(filepath)})


def cmd_run_multi(args):
    """Execute multi-turn FSM test scenarios."""
    try:
        adapter = resolve_adapter(args.adapter, adapter_path=args.adapter_path)
    except Exception as e:
        _exit_error(str(e))

    if not isinstance(adapter, DialogFunctionsAdapter):
        _exit_error("Multi-turn testing requires --adapter dialog")

    handle_fn = adapter.get_handle_fn()
    if handle_fn is None:
        _exit_error("No multi-turn handler found. Looking for async function with (message, session) parameters.")

    normalize_fn = None
    for name in ["_normalize_plan_session", "_normalize_session", "normalize_session"]:
        fn = getattr(adapter.get_module(), name, None)
        if fn:
            normalize_fn = fn
            break

    suite_path = Path(args.suite)
    if not suite_path.exists():
        _exit_error(f"Suite not found: {suite_path}")

    with open(suite_path, "r", encoding="utf-8") as f:
        suite = json.load(f)

    scenario_results = []
    transitions = {}
    total_passed = total_failed = 0

    for scenario in suite.get("scenarios", []):
        result = _run_scenario(scenario, handle_fn, normalize_fn, transitions)
        scenario_results.append(result)
        if result["passed"]:
            total_passed += 1
        else:
            total_failed += 1

    total = len(scenario_results)
    pass_rate = round(total_passed / total, 4) if total else 0
    report = {
        "suite_name": suite.get("name", "unknown"),
        "type": "multi_turn",
        "total_scenarios": total,
        "passed": total_passed, "failed": total_failed,
        "pass_rate": pass_rate,
        "scenarios": scenario_results,
        "transitions_covered": transitions,
    }

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

    _output({"status": "ok", "total_scenarios": total,
             "passed": total_passed, "failed": total_failed,
             "pass_rate": pass_rate, "transitions": len(transitions),
             "report_path": str(args.output) if args.output else None})
    _exit_with_code(pass_rate)


def _run_scenario(scenario, handle_fn, normalize_fn, transitions):
    """Run a single multi-turn scenario."""
    initial = scenario.get("initial_state", {})
    current_session = initial.get("session") or initial.get("plan_session")
    has_context = initial.get("has_context", initial.get("has_plan", False))
    turn_results = []
    scenario_passed = True
    prev_status = "idle"

    for i, turn in enumerate(scenario.get("turns", [])):
        try:
            result = _call_handle_fn(
                handle_fn, normalize_fn,
                user_message=turn["input"],
                session=current_session,
                has_context=has_context,
            )

            current_session = result.get("session") or result.get("plan_session", current_session)
            if current_session and isinstance(current_session, dict):
                current_status = current_session.get("status", "unknown")
            else:
                current_status = "unknown"

            trans_key = f"{prev_status} → {current_status}"
            transitions[trans_key] = transitions.get(trans_key, 0) + 1
            prev_status = current_status

            turn_passed = True
            failures = []
            for key, expected in turn.get("expect", {}).items():
                if key == "handled":
                    actual = result.get("handled")
                elif current_session and isinstance(current_session, dict):
                    actual = current_session.get(key)
                else:
                    actual = None

                if expected == "not_null":
                    if actual is None:
                        turn_passed = False
                        failures.append(f"{key}: expected not_null, got None")
                elif expected == "is_string":
                    if not isinstance(actual, str) or not actual:
                        turn_passed = False
                        failures.append(f"{key}: expected non-empty string, got {actual!r}")
                elif actual != expected:
                    turn_passed = False
                    failures.append(f"{key}: expected {expected!r}, got {actual!r}")

            if not turn_passed:
                scenario_passed = False

            turn_results.append({
                "turn": i + 1, "input": turn["input"],
                "passed": turn_passed, "failures": failures,
                "status_after": current_status,
                "elapsed_ms": result.get("elapsed_ms", 0),
            })
        except Exception as e:
            scenario_passed = False
            turn_results.append({
                "turn": i + 1, "input": turn["input"],
                "passed": False, "error": str(e),
            })

    return {
        "name": scenario["name"],
        "description": scenario.get("description", ""),
        "passed": scenario_passed,
        "turns": turn_results,
    }


def _call_handle_fn(handle_fn, normalize_fn, user_message, session, has_context):
    """Call a multi-turn handler by introspecting its signature.

    Instead of trying hardcoded call variants, we inspect the function's
    parameters and dynamically build the correct kwargs.
    """
    if normalize_fn and session is None:
        try:
            session = normalize_fn(None)
        except Exception:
            pass

    start = time.perf_counter()
    is_async = asyncio.iscoroutinefunction(handle_fn) or inspect.iscoroutinefunction(handle_fn)

    # Introspect signature and build kwargs dynamically
    try:
        sig = inspect.signature(handle_fn)
    except (ValueError, TypeError):
        raise RuntimeError(f"Cannot inspect signature of {handle_fn}")

    kwargs = {}
    for param_name, param in sig.parameters.items():
        # Map parameter names to values based on semantic matching
        name_lower = param_name.lower()

        if name_lower in ("user_message", "message", "input_text",
                          "text", "msg", "input", "user_input"):
            kwargs[param_name] = user_message

        elif name_lower in ("session", "plan_session", "context",
                            "state", "dialog_state", "current_session"):
            kwargs[param_name] = session

        elif name_lower in ("has_plan", "has_context", "existing",
                            "has_existing", "has_active"):
            kwargs[param_name] = has_context

        elif name_lower in ("task_id", "id", "conversation_id",
                            "thread_id", "chat_id"):
            kwargs[param_name] = "test_task_id"

        elif name_lower in ("existing_plan", "current_plan", "plan",
                            "active_plan"):
            kwargs[param_name] = session  # reuse session as plan

        elif param.default is not inspect.Parameter.empty:
            # Has a default — skip, let it use the default
            continue

        else:
            # Unknown required param — pass None and hope for the best
            kwargs[param_name] = None

    try:
        if is_async:
            result = asyncio.run(handle_fn(**kwargs))
        else:
            result = handle_fn(**kwargs)
    except Exception as e:
        raise RuntimeError(f"Handler call failed: {e}")

    elapsed = round((time.perf_counter() - start) * 1000, 2)

    # Normalize result to dict
    if isinstance(result, dict):
        pass
    elif hasattr(result, "__dict__"):
        result = {k: v for k, v in result.__dict__.items() if not k.startswith("_")}
    elif isinstance(result, tuple) and len(result) >= 2:
        result = {"session": result[0], "handled": result[1]}
    else:
        result = {"session": result}

    result["elapsed_ms"] = elapsed

    # Normalize session key — check all common names
    for session_key in ["session", "plan_session", "context", "state"]:
        if session_key in result:
            result["session"] = result[session_key]
            result["plan_session"] = result[session_key]
            break
    else:
        if "status" in result:
            result["session"] = result
            result["plan_session"] = result

    return result


def cmd_report_multi(args):
    """Display multi-turn FSM report."""
    report_path = Path(args.results)
    if not report_path.exists():
        _exit_error(f"Report not found: {report_path}")

    with open(report_path, "r", encoding="utf-8") as f:
        report = json.load(f)

    print(f"\n{'='*60}")
    print(f"  Multi-Turn State Machine Report")
    print(f"{'='*60}")
    print(f"  Suite: {report.get('suite_name', 'unknown')}")
    print(f"  Scenarios: {report['total_scenarios']}")
    print(f"  ✅ Passed: {report['passed']}")
    print(f"  ❌ Failed: {report['failed']}")
    print(f"  Pass Rate: {report['pass_rate']*100:.1f}%")

    failed = [s for s in report.get("scenarios", []) if not s["passed"]]
    if failed:
        print(f"\n  Failed Scenarios:")
        for s in failed:
            print(f"  ❌ {s['name']}: {s.get('description', '')}")
            for t in s.get("turns", []):
                if not t["passed"]:
                    detail = t.get("failures", [t.get("error", "unknown")])
                    print(f"     Turn {t['turn']}: \"{t['input'][:30]}\" → {detail}")

    transitions = report.get("transitions_covered", {})
    if transitions:
        print(f"\n  State Transition Coverage:")
        for trans, count in sorted(transitions.items()):
            print(f"    {trans}: {count}x")

    uncovered = report.get("uncovered_transitions", 0)
    if uncovered:
        print(f"\n  ⚠️  Uncovered transitions: {uncovered}")

    print(f"\n{'='*60}\n")


# ===================================================================
# Unified layered report
# ===================================================================

def cmd_report_unified(args):
    """Display a unified report combining all test layers.

    Reads multiple report files and combines them:
    - Layer 1: routing (run_layer1 output)
    - Layer 2: FSM state transitions (run_multi output)
    - Layer 3: single-function accuracy (run output)
    """
    reports = {}
    for report_file in args.reports:
        path = Path(report_file)
        if not path.exists():
            print(f"  ⚠️  Report not found: {path}")
            continue
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        layer = data.get("layer", data.get("type", data.get("suite_name", path.stem)))
        reports[layer] = data

    if not reports:
        _exit_error("No valid reports found")

    print(f"\n{'='*60}")
    print(f"  Unified Intent Recognition Test Report")
    print(f"{'='*60}")

    total_all = passed_all = failed_all = 0

    for layer_name, data in sorted(reports.items()):
        total = data.get("total", data.get("total_scenarios", 0))
        passed = data.get("passed", 0)
        failed = data.get("failed", 0)
        rate = data.get("pass_rate", 0)

        total_all += total
        passed_all += passed
        failed_all += failed

        badge = "✅" if rate >= 0.8 else "⚠️" if rate >= 0.5 else "❌"
        print(f"\n  {badge} Layer: {layer_name}")
        print(f"     Total: {total}  |  Passed: {passed}  |  Failed: {failed}  |  Rate: {rate*100:.1f}%")

        # Show transitions for multi-turn
        transitions = data.get("transitions_covered", {})
        if transitions:
            print(f"     Transitions covered: {len(transitions)}")

        # Show top failures
        failures = []
        for r in data.get("results", data.get("scenarios", [])):
            if not r.get("passed", True):
                failures.append(r)
        if failures:
            print(f"     Top failures:")
            for f in failures[:3]:
                name = f.get("name", f.get("id", "unknown"))
                detail = f.get("description", f.get("error", ""))
                print(f"       ❌ {name}: {detail[:60]}")

    # Overall summary
    overall_rate = round(passed_all / total_all, 4) if total_all else 0
    badge = "✅" if overall_rate >= 0.8 else "⚠️" if overall_rate >= 0.5 else "❌"

    print(f"\n{'─'*60}")
    print(f"  {badge} Overall: {passed_all}/{total_all} passed ({overall_rate*100:.1f}%)")
    print(f"{'='*60}\n")

    _output({
        "overall_pass_rate": overall_rate,
        "total": total_all,
        "passed": passed_all,
        "failed": failed_all,
        "layers": {name: {"pass_rate": d.get("pass_rate", 0),
                          "total": d.get("total", d.get("total_scenarios", 0))}
                   for name, d in reports.items()},
    })


# ===================================================================
# Commands: FSM Coverage Analysis
# ===================================================================

def cmd_fsm_coverage(args):
    """Analyze FSM state transition coverage from source code."""
    try:
        adapter = resolve_adapter(args.adapter, adapter_path=args.adapter_path)
    except Exception as e:
        _exit_error(str(e))

    if not isinstance(adapter, DialogFunctionsAdapter):
        _exit_error("FSM coverage analysis requires --adapter dialog")

    dialog = adapter.get_module()

    # Discover states from code
    states = set()
    transitions = {}

    # Look for state constants or enums
    for name in dir(dialog):
        val = getattr(dialog, name, None)
        if isinstance(val, str) and name.isupper() and any(
            kw in name.lower() for kw in ["status", "state", "mode"]
        ):
            states.add(val)
        elif isinstance(val, dict) and "status" in str(val).lower():
            for v in val.values():
                if isinstance(v, str):
                    states.add(v)

    # Common FSM states for plan dialog
    default_states = ["idle", "collecting", "await_exit_confirm",
                      "await_offer", "await_confirm", "await_plan_confirm"]
    for s in default_states:
        states.add(s)

    # Discover transitions from function signatures
    func_names = adapter.get_all_functions()
    for func_name in func_names:
        func = getattr(dialog, func_name, None)
        if func:
            try:
                src = inspect.getsource(func)
                # Look for status assignments
                for line in src.split("\n"):
                    if "status" in line and "=" in line:
                        for s in default_states:
                            if s in line:
                                transitions.setdefault(func_name, set()).add(s)
            except (TypeError, OSError):
                pass

    # Check which scenarios cover which transitions
    covered = set()
    for scenario in _BUILTIN_SCENARIOS:
        prev = "idle"
        for turn in scenario.get("turns", []):
            for key, val in turn.get("expect", {}).items():
                if key == "status" and isinstance(val, str):
                    covered.add(f"{prev} → {val}")
                    prev = val

    all_possible = set()
    for s1 in default_states:
        for s2 in default_states:
            if s1 != s2:
                all_possible.add(f"{s1} → {s2}")

    uncovered = all_possible - covered

    result = {
        "states": sorted(states),
        "functions": func_names,
        "covered_transitions": sorted(covered),
        "uncovered_transitions": sorted(uncovered)[:20],
        "coverage_rate": round(len(covered) / len(all_possible), 4) if all_possible else 0,
    }

    print(f"\n{'='*60}")
    print(f"  FSM Coverage Analysis")
    print(f"{'='*60}")
    print(f"  States discovered: {len(states)}")
    for s in sorted(states):
        print(f"    • {s}")
    print(f"\n  Detection functions: {len(func_names)}")
    for f in func_names:
        print(f"    • {f}")
    print(f"\n  Covered transitions: {len(covered)}")
    for t in sorted(covered):
        print(f"    ✅ {t}")
    print(f"\n  Uncovered transitions (top 20): {len(uncovered)}")
    for t in sorted(uncovered)[:20]:
        print(f"    ⚠️  {t}")
    print(f"\n  Coverage rate: {result['coverage_rate']*100:.1f}%")
    print(f"{'='*60}\n")

    _output(result)


# ===================================================================
# Commands: Dependency Check
# ===================================================================

def cmd_check_deps(args):
    """Check project dependencies — available, mocked, missing."""
    results = check_dependencies()

    print(f"\n{'='*60}")
    print(f"  Dependency Check")
    print(f"{'='*60}")

    if results["available"]:
        print(f"\n  ✅ Available ({len(results['available'])}):")
        for m in results["available"]:
            print(f"    {m}")

    if results["mocked"]:
        print(f"\n  🟡 Mocked ({len(results['mocked'])}):")
        for m in results["mocked"]:
            print(f"    {m} (mock — import succeeds but not functional)")

    if results["missing"]:
        print(f"\n  ❌ Missing ({len(results['missing'])}):")
        for m in results["missing"]:
            print(f"    {m}")

    print(f"\n{'='*60}\n")
    _output(results)


# ===================================================================
# Helpers
# ===================================================================

def _resolve_project_root() -> Path:
    """Auto-detect project root by scanning for common markers."""
    current = Path(__file__).resolve().parent
    for _ in range(10):
        # Any of these signals a project root
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


def _output(data: dict):
    """Print JSON output."""
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _exit_error(message: str):
    """Print error and exit with code 3."""
    print(json.dumps({"status": "error", "message": message}, ensure_ascii=False),
          file=sys.stderr)
    sys.exit(3)


def _exit_with_code(pass_rate: float):
    """Exit with standardized code based on pass rate."""
    if pass_rate >= 1.0:
        sys.exit(0)
    elif pass_rate >= 0.5:
        sys.exit(1)
    else:
        sys.exit(2)


# ===================================================================
# CLI
# ===================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Intent Recognition Test Runner — universal adapter"
    )
    parser.add_argument(
        "--adapter", default="auto",
        choices=["auto", "dialog", "rule_engine", "llm_analyzer", "custom"],
        help="Adapter type (default: auto-detect)"
    )
    parser.add_argument("--adapter-path", help="Path for custom/llm_analyzer adapter")

    sub = parser.add_subparsers(dest="command", required=True)

    # Single-turn
    p = sub.add_parser("generate", help="Generate test cases")
    p.add_argument("--name", default="intent_tests")
    p.add_argument("--output-dir", default="tests/generated")

    p = sub.add_parser("run", help="Run test suite")
    p.add_argument("--suite", required=True)
    p.add_argument("--output", help="Save report to file")

    p = sub.add_parser("quick", help="Quick single-input test")
    p.add_argument("--input", required=True)
    p.add_argument("--context", help="State context as JSON (e.g. '{\"status\": \"collecting\"}')")

    p = sub.add_parser("report", help="Display readable report")
    p.add_argument("--results", required=True)

    p = sub.add_parser("template", help="Generate adapter template")
    p.add_argument("--output", default="adapter.py")

    # Multi-turn
    p = sub.add_parser("generate_multi", help="Generate multi-turn FSM scenarios")
    p.add_argument("--name", default="plan_fsm")
    p.add_argument("--output-dir", default="tests/generated")

    p = sub.add_parser("run_multi", help="Run multi-turn FSM tests")
    p.add_argument("--suite", required=True)
    p.add_argument("--output", help="Save report to file")

    p = sub.add_parser("report_multi", help="Display multi-turn FSM report")
    p.add_argument("--results", required=True)

    # Analysis
    p = sub.add_parser("check_deps", help="Check project dependencies")
    p = sub.add_parser("fsm_coverage", help="Analyze FSM state transition coverage")

    # Layer 1 (routing)
    p = sub.add_parser("run_layer1", help="Test LLM routing layer with mocks")
    p.add_argument("--suite", required=True)
    p.add_argument("--config", help="Path to config.json")
    p.add_argument("--output", help="Save report to file")

    # Unified report
    p = sub.add_parser("report_unified", help="Display unified layered report")
    p.add_argument("--reports", nargs="+", required=True,
                   help="Report files to combine")

    args = parser.parse_args()

    commands = {
        "generate": cmd_generate,
        "run": cmd_run,
        "quick": cmd_quick,
        "report": cmd_report,
        "template": cmd_template,
        "generate_multi": cmd_generate_multi,
        "run_multi": cmd_run_multi,
        "report_multi": cmd_report_multi,
        "check_deps": cmd_check_deps,
        "fsm_coverage": cmd_fsm_coverage,
        "run_layer1": cmd_run_layer1,
        "report_unified": cmd_report_unified,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
