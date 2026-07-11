"""Analysis commands: report_unified, fsm_coverage, check_deps."""

import json
import inspect
from pathlib import Path

from common import output, exit_error
from adapters import resolve_adapter, DialogFunctionsAdapter
from mock import check_dependencies
from commands_multi import BUILTIN_SCENARIOS


def cmd_report_unified(args):
    """Display a unified report combining all test layers."""
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
        exit_error("No valid reports found")

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

        transitions = data.get("transitions_covered", {})
        if transitions:
            print(f"     Transitions covered: {len(transitions)}")

        failures = [r for r in data.get("results", data.get("scenarios", []))
                    if not r.get("passed", True)]
        if failures:
            print(f"     Top failures:")
            for f in failures[:3]:
                name = f.get("name", f.get("id", "unknown"))
                detail = f.get("description", f.get("error", ""))
                print(f"       ❌ {name}: {detail[:60]}")

    overall_rate = round(passed_all / total_all, 4) if total_all else 0
    badge = "✅" if overall_rate >= 0.8 else "⚠️" if overall_rate >= 0.5 else "❌"
    print(f"\n{'─'*60}")
    print(f"  {badge} Overall: {passed_all}/{total_all} passed ({overall_rate*100:.1f}%)")
    print(f"{'='*60}\n")

    output({
        "overall_pass_rate": overall_rate,
        "total": total_all, "passed": passed_all, "failed": failed_all,
        "layers": {name: {"pass_rate": d.get("pass_rate", 0),
                          "total": d.get("total", d.get("total_scenarios", 0))}
                   for name, d in reports.items()},
    })


def cmd_fsm_coverage(args):
    """Analyze FSM state transition coverage from source code."""
    try:
        adapter = resolve_adapter(args.adapter, adapter_path=args.adapter_path)
    except Exception as e:
        exit_error(str(e))
    if not isinstance(adapter, DialogFunctionsAdapter):
        exit_error("FSM coverage analysis requires --adapter dialog")

    dialog = adapter.get_module()
    states = set()

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

    default_states = ["idle", "collecting", "await_exit_confirm",
                      "await_offer", "await_confirm", "await_plan_confirm"]
    for s in default_states:
        states.add(s)

    func_names = adapter.get_all_functions()
    transitions = {}
    for func_name in func_names:
        func = getattr(dialog, func_name, None)
        if func:
            try:
                src = inspect.getsource(func)
                for line in src.split("\n"):
                    if "status" in line and "=" in line:
                        for s in default_states:
                            if s in line:
                                transitions.setdefault(func_name, set()).add(s)
            except (TypeError, OSError):
                pass

    covered = set()
    for scenario in BUILTIN_SCENARIOS:
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
        "states": sorted(states), "functions": func_names,
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
    output(result)


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
    output(results)
