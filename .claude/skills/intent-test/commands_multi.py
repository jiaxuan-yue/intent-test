"""Multi-turn FSM test commands: generate_multi, run_multi, report_multi."""

import json
import time
import asyncio
import inspect
from pathlib import Path

from common import output, exit_error, exit_with_code
from adapters import resolve_adapter, DialogFunctionsAdapter


# Built-in scenario templates (Claude fills in project-specific details)
BUILTIN_SCENARIOS = [
    {"name": "happy_path_init", "description": "New session: idle → active → confirmation",
     "initial_state": {"has_context": False, "session": None},
     "turns": [
         {"input": "我想开始", "expect": {"status": "active", "mode": "init", "handled": True}},
         {"input": "每天一次", "expect": {"status": "active", "handled": True}},
     ]},
    {"name": "happy_path_update", "description": "Update existing: idle → active(update) → confirmation",
     "initial_state": {"has_context": True, "session": None},
     "turns": [
         {"input": "我想修改", "expect": {"status": "active", "mode": "update", "handled": True}},
         {"input": "改成两次", "expect": {"status": "active", "handled": True}},
     ]},
    {"name": "exit_confirmed", "description": "User exits → confirmed → idle",
     "initial_state": {"has_context": False, "session": None},
     "turns": [
         {"input": "我想开始", "expect": {"status": "active", "handled": True}},
         {"input": "算了退出", "expect": {"status": "confirm_exit", "handled": True}},
         {"input": "是的退出", "expect": {"status": "idle", "handled": True}},
     ]},
    {"name": "exit_cancelled", "description": "User exits then cancels → resume active",
     "initial_state": {"has_context": False, "session": None},
     "turns": [
         {"input": "我想开始", "expect": {"status": "active", "handled": True}},
         {"input": "算了退出", "expect": {"status": "confirm_exit", "handled": True}},
         {"input": "不继续", "expect": {"status": "active", "handled": True}},
     ]},
    {"name": "offer_accepted", "description": "System offers, user accepts → active",
     "initial_state": {"has_context": False, "session": None},
     "turns": [{"input": "帮我安排", "expect": {"handled": True}}]},
    {"name": "offer_rejected", "description": "Off-topic input → not handled",
     "initial_state": {"has_context": False, "session": None},
     "turns": [{"input": "今天天气怎样", "expect": {"handled": False}}]},
    {"name": "confirm_accepted", "description": "Proposal accepted → active",
     "initial_state": {"has_context": False, "session": None},
     "turns": [{"input": "我想开始", "expect": {"status": "active", "handled": True}}]},
    {"name": "confirm_rejected", "description": "User declines → idle",
     "initial_state": {"has_context": False, "session": None},
     "turns": [{"input": "算了不要了", "expect": {"handled": True}}]},
    {"name": "post_confirm_update", "description": "After confirm → update",
     "initial_state": {"has_context": True, "session": None},
     "turns": [{"input": "修改我的设置", "expect": {"status": "active", "mode": "update", "handled": True}}]},
    {"name": "detail_during_active_no_exit", "description": "Details during active — should NOT exit",
     "initial_state": {"has_context": False, "session": None},
     "turns": [
         {"input": "我想开始", "expect": {"status": "active", "handled": True}},
         {"input": "每天下午3点一次", "expect": {"status": "active", "handled": True}},
     ]},
    {"name": "update_without_context", "description": "Update keyword, no context → fallback init",
     "initial_state": {"has_context": False, "session": None},
     "turns": [{"input": "修改设置", "expect": {"status": "active", "handled": True}}]},
    {"name": "max_turns_auto_complete", "description": "Max turns → auto-generate result",
     "initial_state": {"has_context": False, "session": None},
     "turns": [
         {"input": "我想开始", "expect": {"status": "active", "handled": True}},
         {"input": "每天", "expect": {"status": "active", "handled": True}},
         {"input": "一次", "expect": {"handled": True}},
     ]},
    {"name": "regression_broad_keyword", "description": "Regression: broad keywords false positive",
     "initial_state": {"has_context": False, "session": None},
     "turns": [
         {"input": "随便聊聊", "expect": {"handled": False}},
         {"input": "讲个笑话", "expect": {"handled": False}},
         {"input": "你是谁", "expect": {"handled": False}},
     ]},
    {"name": "regression_negation", "description": "Regression: negation not checked",
     "initial_state": {"has_context": False, "session": None},
     "turns": [
         {"input": "不想开始", "expect": {"handled": False}},
         {"input": "不要修改", "expect": {"handled": False}},
     ]},
    # State injection scenarios
    {"name": "inject_confirm_exit_yes", "description": "From confirm_exit → confirm → idle",
     "initial_state": {"has_context": False, "session": {"status": "confirm_exit", "mode": "", "turns": 0, "messages": []}},
     "turns": [{"input": "是的退出", "expect": {"status": "idle", "handled": True}}]},
    {"name": "inject_confirm_exit_no", "description": "From confirm_exit → cancel → active",
     "initial_state": {"has_context": False, "session": {"status": "confirm_exit", "mode": "", "turns": 0, "messages": []}},
     "turns": [{"input": "不继续", "expect": {"status": "active", "handled": True}}]},
    {"name": "inject_await_offer_accept", "description": "From await_offer → accept → active",
     "initial_state": {"has_context": False, "session": {"status": "await_offer", "mode": "", "turns": 0, "messages": []}},
     "turns": [{"input": "好的开始", "expect": {"status": "active", "handled": True}}]},
    {"name": "inject_await_offer_reject", "description": "From await_offer → reject → idle",
     "initial_state": {"has_context": False, "session": {"status": "await_offer", "mode": "", "turns": 0, "messages": []}},
     "turns": [{"input": "不要了", "expect": {"status": "idle", "handled": True}}]},
    {"name": "inject_active_mid", "description": "From active (2 turns) → continue",
     "initial_state": {"has_context": False, "session": {"status": "active", "mode": "init", "turns": 2, "messages": ["我想开始", "每天一次"]}},
     "turns": [{"input": "下午3点", "expect": {"status": "active", "handled": True}}]},
    {"name": "inject_await_confirm_accept", "description": "From await_confirm → accept → active",
     "initial_state": {"has_context": False, "session": {"status": "await_confirm", "mode": "", "turns": 0, "messages": []}},
     "turns": [{"input": "可以", "expect": {"status": "active", "handled": True}}]},
]


def cmd_generate_multi(args):
    """Generate multi-turn FSM test scenarios."""
    suite = {"name": args.name, "type": "multi_turn", "scenarios": BUILTIN_SCENARIOS}
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    filepath = out_dir / f"{args.name}_multi.json"
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(suite, f, ensure_ascii=False, indent=2)
    output({"status": "ok", "scenario_count": len(BUILTIN_SCENARIOS), "path": str(filepath)})


def cmd_run_multi(args):
    """Execute multi-turn FSM test scenarios."""
    try:
        adapter = resolve_adapter(args.adapter, adapter_path=args.adapter_path)
    except Exception as e:
        exit_error(str(e))

    if not isinstance(adapter, DialogFunctionsAdapter):
        exit_error("Multi-turn testing requires --adapter dialog")

    handle_fn = adapter.get_handle_fn()
    if handle_fn is None:
        exit_error("No multi-turn handler found. Looking for async function with (message, session) parameters.")

    normalize_fn = None
    for name in ["_normalize_plan_session", "_normalize_session", "normalize_session"]:
        fn = getattr(adapter.get_module(), name, None)
        if fn:
            normalize_fn = fn
            break

    # Load state mapping from config (Claude generates this)
    # Maps generic state names → project-specific state names
    # e.g. {"active": "collecting", "confirm_exit": "await_exit_confirm"}
    state_mapping = {}
    if hasattr(args, "config") and args.config:
        from mock import load_config
        config = load_config(args.config)
        state_mapping = config.get("state_mapping", {})

    suite_path = Path(args.suite)
    if not suite_path.exists():
        exit_error(f"Suite not found: {suite_path}")
    with open(suite_path, "r", encoding="utf-8") as f:
        suite = json.load(f)

    scenario_results = []
    transitions = {}
    total_passed = total_failed = 0

    for scenario in suite.get("scenarios", []):
        result = _run_scenario(scenario, handle_fn, normalize_fn, transitions, state_mapping)
        scenario_results.append(result)
        if result["passed"]:
            total_passed += 1
        else:
            total_failed += 1

    total = len(scenario_results)
    pass_rate = round(total_passed / total, 4) if total else 0
    report = {
        "suite_name": suite.get("name", "unknown"), "type": "multi_turn",
        "total_scenarios": total, "passed": total_passed, "failed": total_failed,
        "pass_rate": pass_rate, "scenarios": scenario_results,
        "transitions_covered": transitions,
    }

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

    output({"status": "ok", "total_scenarios": total,
            "passed": total_passed, "failed": total_failed,
            "pass_rate": pass_rate, "transitions": len(transitions),
            "report_path": str(args.output) if args.output else None})
    exit_with_code(pass_rate)


def _run_scenario(scenario, handle_fn, normalize_fn, transitions, state_mapping=None):
    """Run a single multi-turn scenario.

    state_mapping: dict mapping generic state names to project-specific names.
    e.g. {"active": "collecting", "confirm_exit": "await_exit_confirm"}
    """
    if state_mapping is None:
        state_mapping = {}

    def translate_state(generic_name):
        """Translate generic state name to project-specific name."""
        return state_mapping.get(generic_name, generic_name)

    initial = scenario.get("initial_state", {})
    current_session = initial.get("session") or initial.get("plan_session")
    has_context = initial.get("has_context", initial.get("has_plan", False))

    # Translate initial state status if it uses generic names
    if current_session and isinstance(current_session, dict) and "status" in current_session:
        current_session["status"] = translate_state(current_session["status"])

    turn_results = []
    scenario_passed = True
    prev_status = translate_state("idle")

    for i, turn in enumerate(scenario.get("turns", [])):
        try:
            result = _call_handle_fn(handle_fn, normalize_fn,
                                     user_message=turn["input"],
                                     session=current_session,
                                     has_context=has_context)
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

                # Translate expected state names before comparison
                if key == "status" and isinstance(expected, str):
                    expected = translate_state(expected)

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
                "status_after": current_status, "elapsed_ms": result.get("elapsed_ms", 0),
            })
        except Exception as e:
            scenario_passed = False
            turn_results.append({"turn": i + 1, "input": turn["input"],
                                 "passed": False, "error": str(e)})

    return {"name": scenario["name"], "description": scenario.get("description", ""),
            "passed": scenario_passed, "turns": turn_results}


def _call_handle_fn(handle_fn, normalize_fn, user_message, session, has_context):
    """Call multi-turn handler by introspecting its signature."""
    if normalize_fn and session is None:
        try:
            session = normalize_fn(None)
        except Exception:
            pass

    start = time.perf_counter()
    is_async = asyncio.iscoroutinefunction(handle_fn) or inspect.iscoroutinefunction(handle_fn)

    try:
        sig = inspect.signature(handle_fn)
    except (ValueError, TypeError):
        raise RuntimeError(f"Cannot inspect signature of {handle_fn}")

    kwargs = {}
    for param_name, param in sig.parameters.items():
        name_lower = param_name.lower()
        if name_lower in ("user_message", "message", "input_text", "text", "msg", "input", "user_input"):
            kwargs[param_name] = user_message
        elif name_lower in ("session", "plan_session", "context", "state", "dialog_state", "current_session"):
            kwargs[param_name] = session
        elif name_lower in ("has_plan", "has_context", "existing", "has_existing", "has_active"):
            kwargs[param_name] = has_context
        elif name_lower in ("task_id", "id", "conversation_id", "thread_id", "chat_id"):
            kwargs[param_name] = "test_task_id"
        elif name_lower in ("existing_plan", "current_plan", "plan", "active_plan"):
            kwargs[param_name] = session
        elif param.default is not inspect.Parameter.empty:
            continue
        else:
            kwargs[param_name] = None

    try:
        if is_async:
            result = asyncio.run(handle_fn(**kwargs))
        else:
            result = handle_fn(**kwargs)
    except Exception as e:
        raise RuntimeError(f"Handler call failed: {e}")

    elapsed = round((time.perf_counter() - start) * 1000, 2)

    if isinstance(result, dict):
        pass
    elif hasattr(result, "__dict__"):
        result = {k: v for k, v in result.__dict__.items() if not k.startswith("_")}
    elif isinstance(result, tuple) and len(result) >= 2:
        result = {"session": result[0], "handled": result[1]}
    else:
        result = {"session": result}

    result["elapsed_ms"] = elapsed
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
        exit_error(f"Report not found: {report_path}")
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
