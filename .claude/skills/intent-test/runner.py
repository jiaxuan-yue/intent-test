#!/usr/bin/env python3
"""
Intent Recognition Test Runner — universal adapter for any intent system.

Supports multiple project architectures:
  --adapter dialog       Function-based (dialog.py with _detect_*_intent functions)
  --adapter rule_engine  Class-based (RuleEngine.match())
  --adapter custom       User-provided adapter.py with match() function
  --adapter auto         Auto-detect (default, tries all patterns)

Usage:
    python runner.py generate --adapter dialog --name my_tests
    python runner.py run --suite suite.json --adapter dialog
    python runner.py quick --input "我想学Python" --adapter dialog
    python runner.py report --results results.json
    python runner.py template --output adapter.py
"""

import sys
import json
import time
import types
import argparse
import importlib
import importlib.util
from pathlib import Path
from typing import Optional, Callable, Dict, Any, List


# ---------------------------------------------------------------------------
# Adapter: Dialog functions (ChatTutor-style projects)
# ---------------------------------------------------------------------------

def _mock_dependencies():
    """Mock heavy dependencies (langchain_core, langgraph) so dialog.py can be
    imported without installing them — we only need the intent detection functions."""
    mock_modules = {
        "langchain_core": ["messages", "prompts", "language_models", "output_parsers"],
        "langchain_core.messages": ["HumanMessage", "AIMessage", "SystemMessage"],
        "langchain_core.prompts": ["ChatPromptTemplate", "PromptTemplate"],
        "langchain_core.language_models": ["BaseChatModel"],
        "langchain_core.output_parsers": ["JsonOutputParser", "StrOutputParser"],
        "langgraph": ["graph", "prebuilt"],
        "langgraph.graph": ["StateGraph", "MessageGraph", "END"],
        "langgraph.prebuilt": ["ToolNode"],
        "langchain_openai": ["ChatOpenAI"],
        "langchain_community": ["chat_models"],
    }

    for mod_name, attrs in mock_modules.items():
        if mod_name not in sys.modules:
            mock = types.ModuleType(mod_name)
            for attr in attrs:
                mock_cls = type(attr, (), {
                    "__init__": lambda self, *a, **kw: None,
                    "__call__": lambda self, *a, **kw: None,
                    "content": "",
                })
                setattr(mock, attr, mock_cls)
            sys.modules[mod_name] = mock

    # Mock LLM generator module so _get_chat_model() raises → triggers fallback
    # to _next_default_question() and _has_enough_info()
    for gen_mod_name in [
        "app", "app.core", "app.core.task_plan",
        "app.core.task_plan.generator",
    ]:
        if gen_mod_name not in sys.modules:
            sys.modules[gen_mod_name] = types.ModuleType(gen_mod_name)

    mock_gen = sys.modules["app.core.task_plan.generator"]

    def _mock_get_chat_model():
        raise ImportError("Mocked for multi-turn testing — triggers fallback")
    mock_gen._get_chat_model = _mock_get_chat_model


def _import_dialog_functions(project_root: Path = None) -> Dict[str, Callable]:
    """Import intent detection functions from dialog.py (ChatTutor-style).

    Uses importlib to load dialog.py directly, bypassing __init__.py dependency chains.
    Mocks langchain_core/langgraph so only the keyword-matching functions are needed.
    """
    root = project_root or _resolve_project_root()
    _mock_dependencies()

    # Find dialog.py and its dependencies
    candidates = [
        root / "src" / "intent_recognition" / "dialog.py",
        root / "src" / "dialog.py",
        root / "dialog.py",
        root / "src" / "chattutor" / "dialog.py",
    ]

    dialog_path = None
    for c in candidates:
        if c.exists():
            dialog_path = c
            break

    if dialog_path is None:
        # Try finding it by search
        for p in root.rglob("dialog.py"):
            dialog_path = p
            break

    if dialog_path is None:
        raise ImportError("dialog.py not found in project")

    # Load sibling modules that dialog.py might import
    src_dir = dialog_path.parent
    for dep_name in ["prompts", "utils", "models", "config"]:
        dep_path = src_dir / f"{dep_name}.py"
        if dep_path.exists() and dep_name not in sys.modules:
            sys.path.insert(0, str(src_dir))
            try:
                spec = importlib.util.spec_from_file_location(dep_name, str(dep_path))
                mod = importlib.util.module_from_spec(spec)
                sys.modules[dep_name] = mod
                spec.loader.exec_module(mod)
            except Exception:
                pass  # Non-critical dependency

    # Load dialog.py itself
    spec = importlib.util.spec_from_file_location("dialog", str(dialog_path))
    dialog = importlib.util.module_from_spec(spec)
    sys.modules["dialog"] = dialog
    spec.loader.exec_module(dialog)

    # Discover all intent detection functions
    intent_functions = {}
    for name in dir(dialog):
        obj = getattr(dialog, name)
        if callable(obj) and any(kw in name.lower() for kw in
                ["detect", "intent", "is_", "match", "classify", "recognize"]):
            intent_functions[name] = obj

    if not intent_functions:
        raise ImportError(f"No intent detection functions found in {dialog_path}")

    return {
        "_type": "dialog",
        "_module": dialog,
        "_functions": intent_functions,
    }


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

                # Look for keyword lists/dicts
                keywords = {}
                for name in dir(mod):
                    val = getattr(mod, name)
                    if isinstance(val, (list, dict)) and any(
                        kw in name.lower() for kw in ["keyword", "intent", "trigger", "pattern"]
                    ):
                        keywords[name] = val
                if keywords:
                    return keywords
            except Exception:
                pass

    return {}


# ---------------------------------------------------------------------------
# Adapter: RuleEngine (classic class-based)
# ---------------------------------------------------------------------------

def _import_rule_engine(project_root: Path = None) -> Dict[str, Any]:
    """Import RuleEngine class."""
    root = project_root or _resolve_project_root()
    src = root / "src"
    if src.is_dir():
        sys.path.insert(0, str(src))

    from intent_recognition.engine import RuleEngine
    engine = RuleEngine()

    return {
        "_type": "rule_engine",
        "_engine": engine,
    }


# ---------------------------------------------------------------------------
# Adapter: Custom (user-provided adapter.py)
# ---------------------------------------------------------------------------

def _import_custom_adapter(adapter_path: str) -> Dict[str, Any]:
    """Load a custom adapter.py with a match() function."""
    path = Path(adapter_path).resolve()
    if not path.exists():
        raise ImportError(f"Adapter not found: {path}")

    spec = importlib.util.spec_from_file_location("custom_adapter", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    if not hasattr(mod, "match"):
        raise ImportError("Custom adapter must define match(input_text: str) -> dict")

    return {
        "_type": "custom",
        "_match": mod.match,
    }


# ---------------------------------------------------------------------------
# Unified adapter interface
# ---------------------------------------------------------------------------

def load_adapter(adapter_type: str, project_root: Path = None, adapter_path: str = None) -> Dict:
    """Load the appropriate adapter based on type."""
    if adapter_type == "dialog":
        return _import_dialog_functions(project_root)
    elif adapter_type == "rule_engine":
        return _import_rule_engine(project_root)
    elif adapter_type == "custom":
        return _import_custom_adapter(adapter_path)
    elif adapter_type == "auto":
        # Try each adapter type in order
        for atype, loader in [
            ("dialog", lambda: _import_dialog_functions(project_root)),
            ("rule_engine", lambda: _import_rule_engine(project_root)),
        ]:
            try:
                result = loader()
                result["_type"] = atype
                return result
            except (ImportError, Exception):
                continue
        return None
    else:
        raise ValueError(f"Unknown adapter type: {adapter_type}")


def run_single_test(adapter: Dict, input_text: str) -> Dict:
    """Run a single test input through the adapter."""
    start = time.perf_counter()
    atype = adapter["_type"]

    if atype == "dialog":
        # Test all detection functions
        results = {}
        for func_name, func in adapter["_functions"].items():
            try:
                result = func(input_text)
                results[func_name] = {
                    "result": result,
                    "type": type(result).__name__,
                }
            except Exception as e:
                results[func_name] = {"error": str(e)}
        elapsed = round((time.perf_counter() - start) * 1000, 2)
        return {"elapsed_ms": elapsed, "functions": results}

    elif atype == "rule_engine":
        result = adapter["_engine"].match(input_text)
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

    elif atype == "custom":
        result = adapter["_match"](input_text)
        elapsed = round((time.perf_counter() - start) * 1000, 2)
        return {"elapsed_ms": elapsed, **result}

    return {"error": f"Unknown adapter type: {atype}"}


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_generate(args):
    """Generate test cases based on discovered intent system."""
    try:
        adapter = load_adapter(args.adapter, adapter_path=args.adapter_path)
    except Exception as e:
        print(json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False))
        sys.exit(1)

    if adapter is None:
        print(json.dumps({
            "status": "error",
            "message": "No intent system found. Use --adapter custom --adapter-path adapter.py",
        }, ensure_ascii=False))
        sys.exit(1)

    atype = adapter["_type"]
    test_cases = []
    case_id = 0

    if atype == "dialog":
        # Generate tests for each detection function
        functions = adapter["_functions"]

        # Try to extract keywords for smarter test generation
        keywords = _extract_keywords_from_prompts()

        for func_name in functions:
            # Positive: should return True
            if keywords:
                for kw_source, kw_list in keywords.items():
                    if func_name.lower().replace("_", "") in kw_source.lower().replace("_", ""):
                        items = kw_list if isinstance(kw_list, list) else list(kw_list)
                        for kw in items[:5]:
                            case_id += 1
                            test_cases.append({
                                "id": f"pos_{func_name}_{case_id}",
                                "input": kw,
                                "expected_function": func_name,
                                "expected_result": True,
                                "case_type": "positive",
                                "priority": "p0",
                            })

            # Adversarial: empty, negation, noise
            for adv_input, label in [
                ("", "empty"),
                ("不想学", "negation"),
                ("abc" * 100, "long_text"),
                ("🎉🎊", "emoji"),
                ("   ", "whitespace"),
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

            # Boundary: conflicting inputs
            for boundary_input, label in [
                ("是的我要退出", "yes_exit_conflict"),
                ("不想学但是想看计划", "negation_plan"),
            ]:
                case_id += 1
                test_cases.append({
                    "id": f"bnd_{func_name}_{case_id}",
                    "input": boundary_input,
                    "expected_function": func_name,
                    "case_type": "boundary",
                    "priority": "p1",
                    "label": label,
                })

    elif atype == "rule_engine":
        engine = adapter["_engine"]
        rules = getattr(engine, "keyword_rules", [])
        for rule in rules:
            intent = getattr(rule, "intent", "unknown")
            for kw in getattr(rule, "keywords", [])[:3]:
                case_id += 1
                test_cases.append({
                    "id": f"pos_{intent}_{case_id}",
                    "input": kw,
                    "expected_intent": str(intent),
                    "case_type": "positive",
                    "priority": "p0",
                })

    suite = {
        "name": args.name,
        "adapter_type": atype,
        "test_cases": test_cases,
    }

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    filepath = output_dir / f"{args.name}.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(suite, f, ensure_ascii=False, indent=2)

    print(json.dumps({
        "status": "ok",
        "test_count": len(test_cases),
        "adapter_type": atype,
        "path": str(filepath),
    }, ensure_ascii=False))


def cmd_run(args):
    """Run a test suite through the adapter."""
    try:
        adapter = load_adapter(args.adapter, adapter_path=args.adapter_path)
    except Exception as e:
        print(json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False))
        sys.exit(1)

    if adapter is None:
        print(json.dumps({"status": "error", "message": "No intent system found"}, ensure_ascii=False))
        sys.exit(1)

    suite_path = Path(args.suite)
    if not suite_path.exists():
        print(f"[ERROR] Suite not found: {suite_path}", file=sys.stderr)
        sys.exit(1)

    with open(suite_path, "r", encoding="utf-8") as f:
        suite = json.load(f)

    atype = adapter["_type"]
    results = []
    passed = 0
    failed = 0
    errors = 0

    for tc in suite.get("test_cases", []):
        try:
            result = run_single_test(adapter, tc["input"])

            if atype == "dialog":
                # For dialog adapter, check expected function result
                func_name = tc.get("expected_function")
                expected = tc.get("expected_result")
                if func_name and func_name in result.get("functions", {}):
                    actual = result["functions"][func_name].get("result")
                    is_pass = actual == expected
                else:
                    # No specific expectation — just record results
                    is_pass = True

                results.append({
                    "id": tc["id"],
                    "input": tc["input"],
                    "expected_function": func_name,
                    "expected_result": expected,
                    "actual": result.get("functions", {}).get(func_name, {}),
                    "elapsed_ms": result["elapsed_ms"],
                    "passed": is_pass,
                    "case_type": tc.get("case_type"),
                })

            else:
                actual = result.get("intent")
                expected = tc.get("expected_intent")
                is_pass = actual == expected

                results.append({
                    "id": tc["id"],
                    "input": tc["input"],
                    "expected": expected,
                    "actual": actual,
                    "confidence": result.get("confidence", 0.0),
                    "elapsed_ms": result["elapsed_ms"],
                    "passed": is_pass,
                    "case_type": tc.get("case_type"),
                })

            if is_pass:
                passed += 1
            else:
                failed += 1

        except Exception as e:
            errors += 1
            results.append({
                "id": tc["id"],
                "input": tc["input"],
                "error": str(e),
                "passed": False,
            })

    total = len(results)
    report = {
        "suite_name": suite.get("name", "unknown"),
        "adapter_type": atype,
        "total": total,
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "pass_rate": round(passed / total, 4) if total > 0 else 0,
        "results": results,
    }

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

    print(json.dumps({
        "status": "ok",
        "total": total,
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "pass_rate": report["pass_rate"],
        "report_path": str(args.output) if args.output else None,
    }, ensure_ascii=False))

    # Also save full report to stdout if no output file
    if not args.output:
        print(json.dumps(report, ensure_ascii=False, indent=2))


def cmd_quick(args):
    """Quick single-input test across all detection functions."""
    try:
        adapter = load_adapter(args.adapter, adapter_path=args.adapter_path)
    except Exception as e:
        print(json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False))
        sys.exit(1)

    result = run_single_test(adapter, args.input)
    output = {"input": args.input, **result}
    print(json.dumps(output, ensure_ascii=False, indent=2))


def cmd_report(args):
    """Display a readable test report in the terminal."""
    report_path = Path(args.results)
    if not report_path.exists():
        print(f"[ERROR] Report not found: {report_path}", file=sys.stderr)
        sys.exit(1)

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

    # Show failures
    failures = [r for r in report.get("results", []) if not r["passed"]]
    if failures:
        print(f"\n  Failed cases:")
        for r in failures[:10]:
            print(f"    ❌ {r['id']}: \"{r['input'][:40]}\"")
            if "expected" in r:
                print(f"       expected={r['expected']}, actual={r['actual']}")
            if "error" in r:
                print(f"       error: {r['error']}")

    print(f"\n{'='*60}\n")


def cmd_template(args):
    """Generate adapter template."""
    template = '''"""
Intent Recognition Adapter
Edit match() to call your system's API.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

def match(input_text: str) -> dict:
    """
    Returns: {"intent": str|None, "confidence": float, "details": {...}}
    """
    # Example: class-based
    # from intent_recognition.engine import RuleEngine
    # engine = RuleEngine()
    # result = engine.match(input_text)
    # return {"intent": result.intent.value, "confidence": result.confidence, "details": {}}

    # Example: function-based
    # from dialog import detect_intent
    # return detect_intent(input_text)

    raise NotImplementedError("Edit this adapter for your project.")
'''
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(template)
    print(json.dumps({"status": "ok", "path": str(output_path)}, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Multi-turn state machine testing
# ---------------------------------------------------------------------------

_BUILTIN_SCENARIOS = [
    {
        "name": "init_happy_path",
        "description": "New plan — full happy path: idle → collecting(init) → await_plan_confirm",
        "initial_state": {"has_plan": False, "plan_session": None},
        "turns": [
            {"input": "我想学Python",
             "expect": {"status": "collecting", "mode": "init", "handled": True}},
            {"input": "想入门编程，每天1小时",
             "expect": {"status": "collecting", "handled": True}},
        ],
    },
    {
        "name": "update_happy_path",
        "description": "Update existing plan: idle → collecting(update) → await_plan_confirm",
        "initial_state": {"has_plan": True, "plan_session": None},
        "turns": [
            {"input": "我想修改学习计划",
             "expect": {"status": "collecting", "mode": "update", "handled": True}},
            {"input": "改成每天2小时",
             "expect": {"status": "collecting", "handled": True}},
        ],
    },
    {
        "name": "collecting_exit_confirm_yes",
        "description": "User confirms exit during collecting → idle",
        "initial_state": {"has_plan": False, "plan_session": None},
        "turns": [
            {"input": "我想学Python",
             "expect": {"status": "collecting", "handled": True}},
            {"input": "算了不想学了",
             "expect": {"status": "await_exit_confirm", "handled": True}},
            {"input": "是的，退出",
             "expect": {"status": "idle", "handled": True}},
        ],
    },
    {
        "name": "collecting_exit_confirm_no",
        "description": "User cancels exit → back to collecting",
        "initial_state": {"has_plan": False, "plan_session": None},
        "turns": [
            {"input": "我想学Python",
             "expect": {"status": "collecting", "handled": True}},
            {"input": "算了退出吧",
             "expect": {"status": "await_exit_confirm", "handled": True}},
            {"input": "不，继续",
             "expect": {"status": "collecting", "handled": True}},
        ],
    },
    {
        "name": "await_offer_accept",
        "description": "System offers plan, user accepts → collecting",
        "initial_state": {"has_plan": False, "plan_session": None},
        "turns": [
            {"input": "帮我制定个计划",
             "expect": {"handled": True}},
        ],
    },
    {
        "name": "await_offer_reject",
        "description": "System offers plan, user rejects → idle (handled=False)",
        "initial_state": {"has_plan": False, "plan_session": None},
        "turns": [
            {"input": "今天天气怎么样",
             "expect": {"handled": False}},
        ],
    },
    {
        "name": "await_confirm_accept",
        "description": "Plan proposal accepted → collecting for details",
        "initial_state": {"has_plan": False, "plan_session": None},
        "turns": [
            {"input": "我想学Python",
             "expect": {"status": "collecting", "handled": True}},
        ],
    },
    {
        "name": "await_confirm_reject",
        "description": "Plan proposal rejected → idle",
        "initial_state": {"has_plan": False, "plan_session": None},
        "turns": [
            {"input": "算了不学了",
             "expect": {"handled": True}},
        ],
    },
    {
        "name": "plan_confirm_then_update",
        "description": "After plan confirmed, user wants to update → collecting(update)",
        "initial_state": {"has_plan": True, "plan_session": None},
        "turns": [
            {"input": "修改我的学习计划",
             "expect": {"status": "collecting", "mode": "update", "handled": True}},
        ],
    },
    {
        "name": "exit_with_update_details_guard",
        "description": "User provides details during collecting — should NOT trigger exit",
        "initial_state": {"has_plan": False, "plan_session": None},
        "turns": [
            {"input": "我想学Python",
             "expect": {"status": "collecting", "handled": True}},
            {"input": "每天下午2点学1小时",
             "expect": {"status": "collecting", "handled": True}},
        ],
    },
    {
        "name": "update_keyword_no_plan",
        "description": "Update keyword but no plan exists → falls back to init mode",
        "initial_state": {"has_plan": False, "plan_session": None},
        "turns": [
            {"input": "修改学习计划",
             "expect": {"status": "collecting", "handled": True}},
        ],
    },
    {
        "name": "collecting_max_turns",
        "description": "Collecting reaches max turns → auto-generates plan proposal",
        "initial_state": {"has_plan": False, "plan_session": None},
        "turns": [
            {"input": "我想学Python",
             "expect": {"status": "collecting", "handled": True}},
            {"input": "每天学",
             "expect": {"status": "collecting", "handled": True}},
            {"input": "1小时",
             "expect": {"handled": True}},
        ],
    },
]


def cmd_generate_multi(args):
    """Generate multi-turn state machine test scenarios."""
    scenarios = _BUILTIN_SCENARIOS

    suite = {
        "name": args.name,
        "type": "multi_turn",
        "scenarios": scenarios,
    }

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    filepath = output_dir / f"{args.name}_multi.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(suite, f, ensure_ascii=False, indent=2)

    print(json.dumps({
        "status": "ok",
        "scenario_count": len(scenarios),
        "path": str(filepath),
    }, ensure_ascii=False))


def cmd_run_multi(args):
    """Execute multi-turn state machine test scenarios."""
    import asyncio

    suite_path = Path(args.suite)
    if not suite_path.exists():
        print(f"[ERROR] Suite not found: {suite_path}", file=sys.stderr)
        sys.exit(1)

    with open(suite_path, "r", encoding="utf-8") as f:
        suite = json.load(f)

    # Load dialog module with mocks
    try:
        adapter = load_adapter(args.adapter, adapter_path=args.adapter_path)
    except Exception as e:
        print(json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False))
        sys.exit(1)

    if adapter is None or adapter["_type"] != "dialog":
        print(json.dumps({
            "status": "error",
            "message": "Multi-turn testing requires --adapter dialog (needs handle_plan_chat)",
        }, ensure_ascii=False))
        sys.exit(1)

    dialog = adapter["_module"]

    # Find handle_plan_chat and helpers
    handle_fn = getattr(dialog, "handle_plan_chat", None)
    normalize_fn = getattr(dialog, "_normalize_plan_session", None)
    has_enough_info_fn = getattr(dialog, "_has_enough_info", None)

    if handle_fn is None:
        print(json.dumps({
            "status": "error",
            "message": "handle_plan_chat not found in dialog module",
        }, ensure_ascii=False))
        sys.exit(1)

    scenario_results = []
    transitions_covered = {}
    total_passed = 0
    total_failed = 0

    for scenario in suite.get("scenarios", []):
        scenario_result = _run_scenario(
            scenario, dialog, handle_fn, normalize_fn,
            transitions_covered,
        )
        scenario_results.append(scenario_result)
        if scenario_result["passed"]:
            total_passed += 1
        else:
            total_failed += 1

    total_scenarios = len(scenario_results)
    report = {
        "suite_name": suite.get("name", "unknown"),
        "type": "multi_turn",
        "total_scenarios": total_scenarios,
        "passed": total_passed,
        "failed": total_failed,
        "pass_rate": round(total_passed / total_scenarios, 4) if total_scenarios else 0,
        "scenarios": scenario_results,
        "transitions_covered": transitions_covered,
    }

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

    print(json.dumps({
        "status": "ok",
        "total_scenarios": total_scenarios,
        "passed": total_passed,
        "failed": total_failed,
        "pass_rate": report["pass_rate"],
        "transitions": len(transitions_covered),
        "report_path": str(args.output) if args.output else None,
    }, ensure_ascii=False))


def _run_scenario(scenario, dialog, handle_fn, normalize_fn, transitions):
    """Run a single multi-turn scenario, return result dict."""
    import asyncio

    current_session = scenario.get("initial_state", {}).get("plan_session")
    has_plan = scenario.get("initial_state", {}).get("has_plan", False)
    turn_results = []
    scenario_passed = True
    prev_status = "idle"

    for i, turn in enumerate(scenario.get("turns", [])):
        try:
            # Build input for handle_plan_chat
            # The function signature varies by project — try common patterns
            result = _call_handle_plan_chat(
                dialog, handle_fn, normalize_fn,
                user_message=turn["input"],
                plan_session=current_session,
                has_plan=has_plan,
            )

            # Extract state from result
            current_session = result.get("plan_session", current_session)
            if current_session and isinstance(current_session, dict):
                current_status = current_session.get("status", "unknown")
            else:
                current_status = "unknown"

            # Track transition
            trans_key = f"{prev_status} → {current_status}"
            transitions[trans_key] = transitions.get(trans_key, 0) + 1
            prev_status = current_status

            # Validate expectations
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
                "turn": i + 1,
                "input": turn["input"],
                "passed": turn_passed,
                "failures": failures,
                "status_after": current_status,
                "elapsed_ms": result.get("elapsed_ms", 0),
            })

        except Exception as e:
            scenario_passed = False
            turn_results.append({
                "turn": i + 1,
                "input": turn["input"],
                "passed": False,
                "error": str(e),
            })

    return {
        "name": scenario["name"],
        "description": scenario.get("description", ""),
        "passed": scenario_passed,
        "turns": turn_results,
    }


def _call_handle_plan_chat(dialog, handle_fn, normalize_fn,
                            user_message, plan_session, has_plan):
    """Call handle_plan_chat, handling async/sync and various signatures."""
    import asyncio
    import inspect

    # Normalize session if helper available
    if normalize_fn and plan_session is None:
        try:
            plan_session = normalize_fn(None)
        except Exception:
            pass

    start = time.perf_counter()

    # Determine if function is async
    is_async = asyncio.iscoroutinefunction(handle_fn) or inspect.iscoroutinefunction(handle_fn)

    # Try different call signatures
    call_variants = [
        # (kwargs, description)
        ({"user_message": user_message, "plan_session": plan_session, "has_plan": has_plan}, "full"),
        ({"user_message": user_message, "plan_session": plan_session}, "no_has_plan"),
        ({"input_text": user_message, "plan_session": plan_session, "has_plan": has_plan}, "input_text"),
        ({"message": user_message, "session": plan_session}, "message_session"),
    ]

    result = None
    last_error = None

    for kwargs, desc in call_variants:
        try:
            if is_async:
                result = asyncio.run(handle_fn(**kwargs))
            else:
                result = handle_fn(**kwargs)

            # Convert result to dict
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
        raise RuntimeError(f"Could not call handle_plan_chat: {last_error}")

    result["elapsed_ms"] = elapsed

    # Ensure plan_session is accessible
    if "plan_session" not in result and isinstance(result, dict):
        # Maybe the result IS the session
        if "status" in result:
            result["plan_session"] = result

    return result


def cmd_report_multi(args):
    """Display multi-turn state machine test report."""
    report_path = Path(args.results)
    if not report_path.exists():
        print(f"[ERROR] Report not found: {report_path}", file=sys.stderr)
        sys.exit(1)

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

    # Failed scenarios
    failed = [s for s in report.get("scenarios", []) if not s["passed"]]
    if failed:
        print(f"\n  Failed Scenarios:")
        for s in failed:
            print(f"  ❌ {s['name']}: {s.get('description', '')}")
            for t in s.get("turns", []):
                if not t["passed"]:
                    fail_detail = t.get("failures", [t.get("error", "unknown")])
                    print(f"     Turn {t['turn']}: \"{t['input'][:30]}\" → {fail_detail}")

    # State transition coverage
    transitions = report.get("transitions_covered", {})
    if transitions:
        print(f"\n  State Transition Coverage:")
        for trans, count in sorted(transitions.items()):
            print(f"    {trans}: {count}x")

    uncovered_count = report.get("uncovered_transitions", 0)
    if uncovered_count:
        print(f"\n  ⚠️  Uncovered transitions: {uncovered_count}")

    print(f"\n{'='*60}\n")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_project_root() -> Path:
    """Walk up to find project root (has src/ or pyproject.toml)."""
    current = Path(__file__).resolve().parent
    for _ in range(10):
        if (current / "src").is_dir() or (current / "pyproject.toml").exists():
            return current
        current = current.parent
    return Path.cwd()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Intent Recognition Test Runner — universal adapter"
    )
    parser.add_argument(
        "--adapter", default="auto",
        choices=["auto", "dialog", "rule_engine", "custom"],
        help="Adapter type (default: auto-detect)"
    )
    parser.add_argument(
        "--adapter-path",
        help="Path to custom adapter.py (for --adapter custom)"
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # generate
    p_gen = subparsers.add_parser("generate", help="Generate test cases")
    p_gen.add_argument("--name", default="intent_tests")
    p_gen.add_argument("--output-dir", default="tests/generated")

    # run
    p_run = subparsers.add_parser("run", help="Run a test suite")
    p_run.add_argument("--suite", required=True)
    p_run.add_argument("--output", help="Save report to file")

    # quick
    p_quick = subparsers.add_parser("quick", help="Quick single-input test")
    p_quick.add_argument("--input", required=True)

    # report
    p_report = subparsers.add_parser("report", help="Display readable report")
    p_report.add_argument("--results", required=True)

    # template
    p_tpl = subparsers.add_parser("template", help="Generate adapter template")
    p_tpl.add_argument("--output", default="adapter.py")

    # generate_multi
    p_gen_multi = subparsers.add_parser("generate_multi", help="Generate multi-turn FSM scenarios")
    p_gen_multi.add_argument("--name", default="plan_fsm")
    p_gen_multi.add_argument("--output-dir", default="tests/generated")

    # run_multi
    p_run_multi = subparsers.add_parser("run_multi", help="Run multi-turn FSM tests")
    p_run_multi.add_argument("--suite", required=True)
    p_run_multi.add_argument("--output", help="Save report to file")

    # report_multi
    p_report_multi = subparsers.add_parser("report_multi", help="Display multi-turn FSM report")
    p_report_multi.add_argument("--results", required=True)

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
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
