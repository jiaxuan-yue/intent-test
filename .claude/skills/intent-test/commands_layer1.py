"""Layer 1 (LLM routing) test commands."""

import sys
import json
import time
import asyncio
import importlib
from pathlib import Path

from common import output, exit_error, exit_with_code
from mock import load_config, apply_llm_mocks


def cmd_run_layer1(args):
    """Test the LLM routing layer with mock strategies."""
    config = load_config(args.config)
    if not config:
        exit_error("No config found. Run /intent-test first so Claude generates config.json")

    apply_llm_mocks(config)

    routing = config.get("layers", {}).get("routing", {})
    entry_path = routing.get("entry", "")
    params = routing.get("params", {})
    if not entry_path:
        exit_error("No routing entry defined in config")

    module_path, func_name = entry_path.rsplit(".", 1)
    try:
        mod = importlib.import_module(module_path)
        route_fn = getattr(mod, func_name)
    except (ImportError, AttributeError) as e:
        exit_error(f"Cannot import routing: {e}")

    suite_path = Path(args.suite)
    if not suite_path.exists():
        exit_error(f"Suite not found: {suite_path}")
    with open(suite_path, "r", encoding="utf-8") as f:
        suite = json.load(f)

    results = []
    passed = failed = errors = 0

    for tc in suite.get("test_cases", []):
        try:
            start = time.perf_counter()
            call_kwargs = dict(params)
            call_kwargs["user_message"] = tc["input"]
            call_kwargs["message"] = tc["input"]

            if asyncio.iscoroutinefunction(route_fn):
                result = asyncio.run(route_fn(**call_kwargs))
            else:
                result = route_fn(**call_kwargs)

            elapsed = round((time.perf_counter() - start) * 1000, 2)

            if isinstance(result, dict):
                decisions = result
            elif hasattr(result, "__dict__"):
                decisions = {k: v for k, v in result.__dict__.items() if not k.startswith("_")}
            else:
                decisions = {"result": result}

            expect = tc.get("expect_routing", {})
            is_pass = True
            failures = []
            for key, expected in expect.items():
                actual = decisions.get(key)
                if actual != expected:
                    is_pass = False
                    failures.append(f"{key}: expected {expected!r}, got {actual!r}")

            results.append({
                "id": tc["id"], "input": tc["input"],
                "decisions": decisions, "elapsed_ms": elapsed,
                "passed": is_pass, "failures": failures,
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
    pass_rate = round(passed / total, 4) if total else 0
    report = {
        "layer": "routing", "total": total, "passed": passed,
        "failed": failed, "errors": errors, "pass_rate": pass_rate,
        "results": results,
    }

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

    output({"status": "ok", "layer": "routing",
            "total": total, "passed": passed, "failed": failed,
            "pass_rate": pass_rate,
            "report_path": str(args.output) if args.output else None})
    exit_with_code(pass_rate)
