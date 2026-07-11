#!/usr/bin/env python3
"""
Intent Recognition Test Runner — universal adapter for any intent system.

Usage:
    # With adapter script (Claude generates this after analyzing your project)
    python runner.py run --suite suite.json --adapter adapter.py

    # Auto-discover: tries common import patterns
    python runner.py run --suite suite.json

    # Quick test
    python runner.py quick --input "你好" --adapter adapter.py

    # Generate adapter template
    python runner.py template --output adapter.py

Adapter contract:
    The adapter.py file must define a function:
        def match(input_text: str) -> dict
    That returns:
        {"intent": str|None, "confidence": float, "details": {...}}
"""

import sys
import json
import time
import argparse
import importlib.util
from pathlib import Path
from typing import Optional, Callable, Dict, Any


# ---------------------------------------------------------------------------
# Adapter discovery
# ---------------------------------------------------------------------------

def load_adapter(adapter_path: str) -> Callable:
    """Load a match() function from an adapter script."""
    path = Path(adapter_path).resolve()
    if not path.exists():
        print(f"[ERROR] Adapter not found: {path}", file=sys.stderr)
        sys.exit(1)

    spec = importlib.util.spec_from_file_location("adapter", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    if not hasattr(module, "match"):
        print(f"[ERROR] Adapter must define a match(input_text: str) -> dict function", file=sys.stderr)
        sys.exit(1)

    return module.match


def auto_discover() -> Optional[Callable]:
    """Try to auto-discover a match() function from common project patterns."""
    src = Path("src")
    if not src.is_dir():
        sys.path.insert(0, str(Path.cwd()))
    else:
        sys.path.insert(0, str(src))

    # Strategy 1: Look for common module patterns
    discovery_patterns = [
        # (module_path, class_or_function_name, is_class)
        ("intent_recognition.engine", "RuleEngine", True),
        ("intent_recognition.engine.rule_engine", "RuleEngine", True),
        ("intent_recognition.recognizer", "IntentRecognizer", True),
        ("intent_recognition.classifier", "IntentClassifier", True),
        ("engine", "RuleEngine", True),
        ("recognizer", "Recognizer", True),
        ("classifier", "Classifier", True),
    ]

    for mod_path, name, is_class in discovery_patterns:
        try:
            mod = importlib.import_module(mod_path)
            obj = getattr(mod, name, None)
            if obj is None:
                continue

            if is_class:
                instance = obj()
                if hasattr(instance, "match"):
                    def adapter(text, eng=instance):
                        result = eng.match(text)
                        intent = getattr(result, "intent", None)
                        if intent is not None and hasattr(intent, "value"):
                            intent = intent.value
                        return {
                            "intent": intent or getattr(result, "detected_intent", None),
                            "confidence": getattr(result, "confidence", 0.0),
                            "details": {
                                "matched_keywords": getattr(result, "matched_keywords", []),
                                "matched_patterns": getattr(result, "matched_patterns", []),
                            }
                        }
                    return adapter
            else:
                def adapter(text, fn=obj):
                    result = fn(text)
                    if isinstance(result, dict):
                        return {
                            "intent": result.get("intent") or result.get("detected_intent"),
                            "confidence": result.get("confidence", 0.0),
                            "details": result,
                        }
                    return {"intent": str(result), "confidence": 1.0, "details": {}}
                return adapter

        except (ImportError, Exception):
            continue

    # Strategy 2: Look for function-based patterns
    func_patterns = [
        ("intent_recognition.dialog", "detect_intent"),
        ("intent_recognition.dialog", "_detect_plan_intent"),
        ("dialog", "detect_intent"),
        ("intent", "recognize"),
        ("nlp", "classify"),
    ]

    for mod_path, func_name in func_patterns:
        try:
            mod = importlib.import_module(mod_path)
            fn = getattr(mod, func_name, None)
            if fn and callable(fn):
                def adapter(text, f=fn):
                    result = f(text)
                    if isinstance(result, dict):
                        return {
                            "intent": result.get("intent") or result.get("detected_intent"),
                            "confidence": result.get("confidence", 0.0),
                            "details": result,
                        }
                    if hasattr(result, "intent"):
                        intent = result.intent
                        if hasattr(intent, "value"):
                            intent = intent.value
                        return {
                            "intent": intent,
                            "confidence": getattr(result, "confidence", 0.0),
                            "details": {},
                        }
                    return {"intent": str(result), "confidence": 1.0, "details": {}}
                return adapter
        except (ImportError, Exception):
            continue

    return None


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_run(args):
    """Run a test suite."""
    # Resolve match function
    if args.adapter:
        match_fn = load_adapter(args.adapter)
    else:
        match_fn = auto_discover()
        if match_fn is None:
            print(json.dumps({
                "status": "error",
                "message": "No intent recognition module found. "
                           "Provide an adapter with --adapter adapter.py, "
                           "or run /intent-test to let Claude generate one.",
            }, ensure_ascii=False))
            sys.exit(1)

    # Load suite
    suite_path = Path(args.suite)
    if not suite_path.exists():
        print(f"[ERROR] Suite not found: {suite_path}", file=sys.stderr)
        sys.exit(1)

    with open(suite_path, "r", encoding="utf-8") as f:
        suite = json.load(f)

    # Execute
    results = []
    passed = 0
    failed = 0
    errors = 0

    for tc in suite.get("test_cases", []):
        try:
            start = time.perf_counter()
            result = match_fn(tc["input"])
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)

            actual = result.get("intent") if isinstance(result, dict) else str(result)
            expected = tc.get("expected_intent")
            is_pass = actual == expected

            results.append({
                "id": tc["id"],
                "input": tc["input"],
                "expected": expected,
                "actual": actual,
                "confidence": result.get("confidence", 0.0) if isinstance(result, dict) else 0.0,
                "details": result.get("details", {}) if isinstance(result, dict) else {},
                "elapsed_ms": elapsed_ms,
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
                "expected": tc.get("expected_intent"),
                "actual": None,
                "error": str(e),
                "passed": False,
            })

    total = len(results)
    report = {
        "suite_name": suite.get("name", "unknown"),
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
        "results": results if not args.output else None,
    }, ensure_ascii=False, indent=2))


def cmd_quick(args):
    """Quick single-input test."""
    if args.adapter:
        match_fn = load_adapter(args.adapter)
    else:
        match_fn = auto_discover()
        if match_fn is None:
            print(json.dumps({
                "status": "error",
                "message": "No intent module found. Use --adapter adapter.py",
            }, ensure_ascii=False))
            sys.exit(1)

    start = time.perf_counter()
    result = match_fn(args.input)
    elapsed_ms = round((time.perf_counter() - start) * 1000, 2)

    output = {
        "input": args.input,
        "result": result,
        "elapsed_ms": elapsed_ms,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


def cmd_template(args):
    """Generate an adapter template file."""
    template = '''"""
Intent Recognition Adapter
===========================
This adapter bridges the intent-test skill with your intent recognition system.

Edit the match() function below to call your system's actual API.
Claude will generate this file automatically after analyzing your project.
"""

import sys
from pathlib import Path

# Add your project source to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def match(input_text: str) -> dict:
    """
    Process an input and return intent recognition result.

    Args:
        input_text: The user input text to classify.

    Returns:
        dict with keys:
            - intent: str or None (the detected intent name)
            - confidence: float 0.0-1.0
            - details: dict (any extra info like matched keywords)
    """
    # === EDIT BELOW to call your system ===

    # Example 1: Class-based engine
    # from intent_recognition.engine import RuleEngine
    # engine = RuleEngine()
    # result = engine.match(input_text)
    # return {
    #     "intent": result.intent.value if result.intent else None,
    #     "confidence": result.confidence,
    #     "details": {"matched_keywords": result.matched_keywords},
    # }

    # Example 2: Function-based
    # from intent_recognition.dialog import detect_intent
    # result = detect_intent(input_text)
    # return {
    #     "intent": result.get("intent"),
    #     "confidence": result.get("confidence", 0.0),
    #     "details": result,
    # }

    # Example 3: LLM-based analyzer
    # from my_app.analyzer import analyze_intent
    # result = analyze_intent(input_text)
    # return {
    #     "intent": result["plan"]["intent"],
    #     "confidence": result["plan"]["confidence"],
    #     "details": result,
    # }

    raise NotImplementedError(
        "Edit this adapter to call your intent recognition system. "
        "Or run /intent-test and let Claude generate it for you."
    )
'''

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(template)

    print(json.dumps({
        "status": "ok",
        "message": f"Adapter template written to {output_path}",
        "path": str(output_path),
    }, ensure_ascii=False))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Intent Recognition Test Runner — universal adapter"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # run
    p_run = subparsers.add_parser("run", help="Run a test suite")
    p_run.add_argument("--suite", required=True, help="Path to test suite JSON")
    p_run.add_argument("--adapter", help="Path to adapter.py (match function)")
    p_run.add_argument("--output", help="Path to save report JSON")

    # quick
    p_quick = subparsers.add_parser("quick", help="Quick single-input test")
    p_quick.add_argument("--input", required=True, help="Input text to test")
    p_quick.add_argument("--adapter", help="Path to adapter.py")

    # template
    p_tpl = subparsers.add_parser("template", help="Generate adapter template")
    p_tpl.add_argument("--output", default="adapter.py", help="Output path")

    args = parser.parse_args()

    commands = {
        "run": cmd_run,
        "quick": cmd_quick,
        "template": cmd_template,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
