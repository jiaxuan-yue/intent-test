#!/usr/bin/env python3
"""
Intent Recognition Test Runner — universal adapter for any intent system.

Architecture:
    BaseAdapter (interface)
    ├── DialogFunctionsAdapter   — function-based (dialog.py, _detect_*_intent)
    ├── RuleEngineAdapter        — class-based (RuleEngine.match())
    ├── LLMAnalyzerAdapter       — LLM structured output (ExecutionPlan, etc.)
    └── CustomAdapter            — user-provided adapter.py with match()

Commands:
    generate          Generate single-turn test cases
    run               Run single-turn test suite
    quick             Quick single-input test
    report            Display readable report
    template          Generate adapter template
    generate_multi    Generate multi-turn FSM scenarios
    run_multi         Run multi-turn FSM tests
    report_multi      Display multi-turn FSM report
    check_deps        Check project dependencies
    fsm_coverage      Analyze FSM state transition coverage

Exit codes:
    0  All tests passed
    1  Some failures, pass_rate >= 0.5
    2  Heavy failures, pass_rate < 0.5
    3  Runtime error (import failure, etc.)
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
                if "test" not in str(p) and "__pycache__" not in str(p):
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
        funcs = {}
        for name in dir(self.dialog):
            obj = getattr(self.dialog, name)
            if callable(obj) and any(kw in name.lower() for kw in
                    ["detect", "intent", "is_", "match", "classify",
                     "recognize", "has_", "plan", "handle"]):
                funcs[name] = obj
        if not funcs:
            raise ImportError("No intent detection functions found in dialog.py")
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
        # Look for common multi-turn handler names
        for name in ["handle_plan_chat", "handle_dialog", "handle_turn",
                      "process_message", "handle_message"]:
            fn = getattr(self.dialog, name, None)
            if fn and (asyncio.iscoroutinefunction(fn) or callable(fn)):
                return fn
        return None

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
        for name in ["handle_plan_chat", "handle_dialog", "handle_turn",
                      "process_message", "handle_message"]:
            fn = getattr(self._mod, name, None)
            if fn and callable(fn):
                return fn
        return None


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
        for name in ["handle_plan_chat", "handle_dialog", "handle_turn",
                      "process_message", "handle_message"]:
            fn = getattr(self._mod, name, None)
            if fn and callable(fn):
                return fn
        return None


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
        _exit_error("No multi-turn handler found in dialog module (looked for handle_plan_chat, handle_dialog, handle_turn, process_message)")

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
    """Call a multi-turn handler, handling async/sync and various signatures."""
    if normalize_fn and session is None:
        try:
            session = normalize_fn(None)
        except Exception:
            pass

    start = time.perf_counter()
    is_async = asyncio.iscoroutinefunction(handle_fn) or inspect.iscoroutinefunction(handle_fn)

    call_variants = [
        {"user_message": user_message, "plan_session": session, "has_plan": has_context},
        {"user_message": user_message, "session": session, "has_context": has_context},
        {"user_message": user_message, "plan_session": session},
        {"user_message": user_message, "session": session},
        {"input_text": user_message, "plan_session": session, "has_plan": has_context},
        {"message": user_message, "session": session},
    ]

    result = None
    last_error = None
    for kwargs in call_variants:
        try:
            if is_async:
                result = asyncio.run(handle_fn(**kwargs))
            else:
                result = handle_fn(**kwargs)
            if isinstance(result, dict):
                break
            elif hasattr(result, "__dict__"):
                result = {k: v for k, v in result.__dict__.items() if not k.startswith("_")}
                break
            elif isinstance(result, tuple) and len(result) >= 2:
                result = {"plan_session": result[0], "handled": result[1]}
                break
            else:
                result = {"plan_session": result}
                break
        except TypeError:
            continue
        except Exception as e:
            last_error = e
            continue

    elapsed = round((time.perf_counter() - start) * 1000, 2)
    if result is None:
        raise RuntimeError(f"Could not call multi-turn handler: {last_error}")

    result["elapsed_ms"] = elapsed
    # Normalize session key
    if "session" not in result and "plan_session" in result:
        result["session"] = result["plan_session"]
    elif "plan_session" not in result and "session" in result:
        result["plan_session"] = result["session"]
    elif "session" not in result and "plan_session" not in result and "status" in result:
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
    current = Path(__file__).resolve().parent
    for _ in range(10):
        if ((current / "src").is_dir() or (current / "pyproject.toml").exists()
                or (current / "app").is_dir()):
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
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
