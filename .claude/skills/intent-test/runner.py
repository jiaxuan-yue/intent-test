#!/usr/bin/env python3
"""
Intent Recognition Test Runner — CLI entry point.

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

Exit codes: 0=pass | 1=partial(≥50%) | 2=fail(<50%) | 3=error
"""

import sys
import argparse
from pathlib import Path

# Add this directory to path so sibling modules can be imported directly
_this_dir = str(Path(__file__).resolve().parent)
if _this_dir not in sys.path:
    sys.path.insert(0, _this_dir)

from common import resolve_project_root, output, exit_error, exit_with_code
from mock import mock_all_dependencies, check_dependencies, load_config, apply_llm_mocks
from extract import extract_keywords_from_prompts
from adapters import (
    BaseAdapter, DialogFunctionsAdapter, RuleEngineAdapter,
    LLMAnalyzerAdapter, CustomAdapter, resolve_adapter,
    find_handler_by_signature,
)
from commands_single import cmd_generate, cmd_run, cmd_quick, cmd_report, cmd_template
from commands_multi import cmd_generate_multi, cmd_run_multi, cmd_report_multi
from commands_layer1 import cmd_run_layer1
from commands_analysis import cmd_report_unified, cmd_fsm_coverage, cmd_check_deps


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
    p.add_argument("--context", help="State context as JSON")

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

    # Layer 1
    p = sub.add_parser("run_layer1", help="Test LLM routing layer with mocks")
    p.add_argument("--suite", required=True)
    p.add_argument("--config", help="Path to config.json")
    p.add_argument("--output", help="Save report to file")

    # Analysis
    p = sub.add_parser("check_deps", help="Check project dependencies")
    p = sub.add_parser("fsm_coverage", help="Analyze FSM state transition coverage")

    p = sub.add_parser("report_unified", help="Display unified layered report")
    p.add_argument("--reports", nargs="+", required=True, help="Report files to combine")

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
