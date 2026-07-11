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
                # Create a no-op class/function for each attribute
                mock_cls = type(attr, (), {
                    "__init__": lambda self, *a, **kw: None,
                    "__call__": lambda self, *a, **kw: None,
                    "content": "",
                })
                setattr(mock, attr, mock_cls)
            sys.modules[mod_name] = mock


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

    args = parser.parse_args()

    commands = {
        "generate": cmd_generate,
        "run": cmd_run,
        "quick": cmd_quick,
        "report": cmd_report,
        "template": cmd_template,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
