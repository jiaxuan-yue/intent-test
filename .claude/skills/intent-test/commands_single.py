"""Single-turn test commands: generate, run, quick, report, template."""

import json
from pathlib import Path

from common import output, exit_error, exit_with_code
from adapters import (
    resolve_adapter, DialogFunctionsAdapter, RuleEngineAdapter,
)


def cmd_generate(args):
    """Generate single-turn test cases."""
    try:
        adapter = resolve_adapter(args.adapter, adapter_path=args.adapter_path)
    except Exception as e:
        exit_error(str(e))

    if adapter is None:
        exit_error("No intent system found. Use --adapter custom --adapter-path adapter.py")

    test_cases = []
    case_id = 0
    keywords = adapter.get_keyword_sets()
    func_names = adapter.get_all_functions()

    if isinstance(adapter, DialogFunctionsAdapter):
        for func_name in func_names:
            if "handle" in func_name.lower():
                continue
            for kw_source, kw_list in keywords.items():
                if func_name.lower().replace("_", "") in kw_source.lower().replace("_", ""):
                    for kw in (kw_list if isinstance(kw_list, list) else list(kw_list))[:5]:
                        case_id += 1
                        test_cases.append({
                            "id": f"pos_{func_name}_{case_id}", "input": kw,
                            "expected_function": func_name, "expected_result": True,
                            "case_type": "positive", "priority": "p0",
                        })
            for adv_input, label in [
                ("", "empty"), ("不想学", "negation"), ("abc" * 100, "long_text"),
                ("🎉🎊", "emoji"), ("   ", "whitespace"), ("今天天气不错", "off_topic"),
                ("周末去看电影", "time_false_positive"), ("不计划了", "negation_update"),
            ]:
                case_id += 1
                test_cases.append({
                    "id": f"adv_{func_name}_{case_id}", "input": adv_input,
                    "expected_function": func_name, "expected_result": False,
                    "case_type": "adversarial", "priority": "p1", "label": label,
                })
            for b_input, label in [
                ("是的我要退出", "yes_exit_conflict"),
                ("不想学但是想看计划", "negation_plan"),
                ("好的我不想", "yes_no_mixed"),
            ]:
                case_id += 1
                test_cases.append({
                    "id": f"bnd_{func_name}_{case_id}", "input": b_input,
                    "expected_function": func_name,
                    "case_type": "boundary", "priority": "p1", "label": label,
                })

    elif isinstance(adapter, RuleEngineAdapter):
        for intent, kws in keywords.items():
            for kw in kws[:3]:
                case_id += 1
                test_cases.append({
                    "id": f"pos_{intent}_{case_id}", "input": kw,
                    "expected_intent": intent, "case_type": "positive", "priority": "p0",
                })

    suite = {"name": args.name, "adapter_type": adapter.get_type(), "test_cases": test_cases}
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    filepath = out_dir / f"{args.name}.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(suite, f, ensure_ascii=False, indent=2)

    output({"status": "ok", "test_count": len(test_cases),
            "adapter_type": adapter.get_type(), "path": str(filepath)})


def cmd_run(args):
    """Run single-turn test suite."""
    try:
        adapter = resolve_adapter(args.adapter, adapter_path=args.adapter_path)
    except Exception as e:
        exit_error(str(e))
    if adapter is None:
        exit_error("No intent system found")

    suite_path = Path(args.suite)
    if not suite_path.exists():
        exit_error(f"Suite not found: {suite_path}")
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

    output({"status": "ok", "total": total, "passed": passed,
            "failed": failed, "errors": errors, "pass_rate": pass_rate,
            "report_path": str(args.output) if args.output else None})
    exit_with_code(pass_rate)


def cmd_quick(args):
    """Quick single-input test."""
    try:
        adapter = resolve_adapter(args.adapter, adapter_path=args.adapter_path)
    except Exception as e:
        exit_error(str(e))

    context = None
    if args.context:
        try:
            context = json.loads(args.context)
        except json.JSONDecodeError:
            exit_error(f"Invalid JSON for --context: {args.context}")

    result = adapter.detect_intents(args.input, context)
    output({
        "input": args.input, "context": context,
        "functions": adapter.get_all_functions(), **result,
    })


def cmd_report(args):
    """Display readable single-turn report."""
    report_path = Path(args.results)
    if not report_path.exists():
        exit_error(f"Report not found: {report_path}")
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

KEYWORDS = {}
FUNCTIONS = ["match"]
'''
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(template)
    output({"status": "ok", "path": str(out)})
