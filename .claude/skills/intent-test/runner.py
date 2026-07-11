#!/usr/bin/env python3
"""
Intent Recognition Test Runner — helper script for the intent-test skill.

Usage:
    python runner.py generate --intents '{"greeting": ["你好", "hi"]}' [--name suite] [--output-dir dir]
    python runner.py run --suite tests/generated/suite.json
    python runner.py report --suite tests/generated/suite.json --results tests/generated/results.json
"""

import sys
import json
import time
import argparse
from pathlib import Path
from typing import Optional


def _resolve_project_root() -> Path:
    """Walk up from this file to find the project root (has src/ or pyproject.toml)."""
    current = Path(__file__).resolve().parent
    for _ in range(10):
        if (current / "src").is_dir() or (current / "pyproject.toml").exists():
            return current
        current = current.parent
    return Path.cwd()


def _import_engine():
    """Import RuleEngine from the project."""
    root = _resolve_project_root()
    src = root / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))
    try:
        from intent_recognition.engine import RuleEngine
        return RuleEngine
    except ImportError as e:
        print(f"[ERROR] Cannot import RuleEngine: {e}", file=sys.stderr)
        print("Ensure src/intent_recognition/engine/ exists and is importable.", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_generate(args):
    """Generate test cases using the project's test framework."""
    try:
        from intent_recognition.testing import (
            TestCaseGeneratorFactory,
            UniversalTestCaseGenerator,
        )
        from intent_recognition.testing.types import TestCaseType
    except ImportError as e:
        print(f"[ERROR] Cannot import testing module: {e}", file=sys.stderr)
        sys.exit(1)

    intents = None
    if args.intents:
        try:
            intents = json.loads(args.intents)
        except json.JSONDecodeError as e:
            print(f"[ERROR] Invalid JSON for --intents: {e}", file=sys.stderr)
            sys.exit(1)

    if intents:
        generator = TestCaseGeneratorFactory.create_for_custom_module(
            intents=intents, config={"extract_from_engine": False}
        )
    else:
        RuleEngine = _import_engine()
        try:
            engine = RuleEngine()
            generator = TestCaseGeneratorFactory.create_for_rule_engine(engine)
        except Exception as e:
            print(f"[ERROR] Cannot auto-extract from engine: {e}", file=sys.stderr)
            print("Provide intents manually with --intents", file=sys.stderr)
            sys.exit(1)

    suite = generator.generate_suite(name=args.name)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    filepath = generator.save_suite(suite)

    print(json.dumps({
        "status": "ok",
        "test_count": len(suite.test_cases),
        "path": str(filepath),
        "name": args.name,
    }, ensure_ascii=False))


def cmd_run(args):
    """Run a test suite against the engine."""
    suite_path = Path(args.suite)
    if not suite_path.exists():
        print(f"[ERROR] Suite not found: {suite_path}", file=sys.stderr)
        sys.exit(1)

    with open(suite_path, "r", encoding="utf-8") as f:
        suite = json.load(f)

    RuleEngine = _import_engine()
    engine = RuleEngine()

    results = []
    passed = 0
    failed = 0
    errors = 0

    for tc in suite.get("test_cases", []):
        try:
            start = time.perf_counter()
            result = engine.match(tc["input"])
            elapsed_ms = round((time.perf_counter() - start) * 1000, 2)

            actual_intent = result.intent.value if result.intent else None
            expected = tc.get("expected_intent")
            is_pass = actual_intent == expected

            results.append({
                "id": tc["id"],
                "input": tc["input"],
                "expected": expected,
                "actual": actual_intent,
                "confidence": result.confidence,
                "matched_keywords": getattr(result, "matched_keywords", []),
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

    # Save if output path given
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
            "report_path": str(output_path),
        }, ensure_ascii=False))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))


def cmd_quick(args):
    """Quick single-input test."""
    RuleEngine = _import_engine()
    engine = RuleEngine()

    start = time.perf_counter()
    result = engine.match(args.input)
    elapsed_ms = round((time.perf_counter() - start) * 1000, 2)

    output = {
        "input": args.input,
        "intent": result.intent.value if result.intent else None,
        "confidence": result.confidence,
        "matched_keywords": getattr(result, "matched_keywords", []),
        "matched_patterns": getattr(result, "matched_patterns", []),
        "elapsed_ms": elapsed_ms,
    }

    # Check if LLM fallback needed
    if hasattr(engine, "quick_detect"):
        quick = engine.quick_detect(args.input)
        if quick.get("needs_llm"):
            output["llm_suggestion"] = quick.get("hint")

    print(json.dumps(output, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Intent Recognition Test Runner")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # generate
    p_gen = subparsers.add_parser("generate", help="Generate test cases")
    p_gen.add_argument("--intents", help="Custom intents as JSON string")
    p_gen.add_argument("--name", default="intent_tests", help="Suite name")
    p_gen.add_argument("--output-dir", default="tests/generated", help="Output directory")

    # run
    p_run = subparsers.add_parser("run", help="Run a test suite")
    p_run.add_argument("--suite", required=True, help="Path to test suite JSON")
    p_run.add_argument("--output", help="Path to save report JSON")

    # quick
    p_quick = subparsers.add_parser("quick", help="Quick single-input test")
    p_quick.add_argument("--input", required=True, help="Input text to test")

    args = parser.parse_args()

    commands = {
        "generate": cmd_generate,
        "run": cmd_run,
        "quick": cmd_quick,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
