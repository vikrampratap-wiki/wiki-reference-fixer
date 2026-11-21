#!/usr/bin/env python3
"""
Standalone Opaque-Box E2E Test Runner for Hindi Wikipedia Reference Fixer.
Discovers and executes tests across all 4 Tiers, formats structured reports,
and exits with code 0 on success or 1 on failure.
"""

import argparse
import inspect
import os
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from e2e_tests.client import E2ETestClient  # noqa: E402
import e2e_tests.tier1_feature_coverage as t1  # noqa: E402
import e2e_tests.tier2_boundary_corner as t2  # noqa: E402
import e2e_tests.tier3_pairwise as t3  # noqa: E402
import e2e_tests.tier4_real_world as t4  # noqa: E402

# ANSI Color Codes
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def discover_tier_tests(module: Any) -> List[Tuple[str, Callable, str]]:
    """Discovers test functions from a given module, returning (name, func, docstring)."""
    tests = []
    for attr_name in dir(module):
        if attr_name.startswith("test_"):
            func = getattr(module, attr_name)
            if callable(func):
                doc = (inspect.getdoc(func) or "").split("\n")[0].strip()
                tests.append((attr_name, func, doc))
    # Keep deterministic alphabetical order
    return sorted(tests, key=lambda x: x[0])


def run_test_suite(
    target: str = "contract",
    base_url: Optional[str] = None,
    tier_filter: str = "all",
    verbose: bool = False,
    list_only: bool = False,
) -> int:

    client = E2ETestClient(target=target, base_url=base_url)

    sep_line = "=" * 80
    dash_line = "-" * 80

    tiers = [
        (
            1,
            "Tier 1: Feature Coverage (AST, Bare URLs, Syntax, RBAC, Helpers, Save, Admin)",
            t1,
        ),
        (
            2,
            "Tier 2: Boundary & Corner Cases (Empty text, nesting, malformed URLs, special)",
            t2,
        ),
        (
            3,
            "Tier 3: Cross-Feature Interactions (Pairwise multi-feature pipelines)",
            t3,
        ),
        (
            4,
            "Tier 4: Real-World Scenarios (चन्द्रयान-3, महात्मा गांधी, भारत, विकिपीडिया)",
            t4,
        ),
    ]

    print(f"\n{BOLD}{CYAN}{sep_line}{RESET}")
    title_text = "Hindi Wikipedia Reference Fixer — Dual Track Opaque-Box E2E Test Suite"
    print(f"{BOLD}{CYAN} {title_text}{RESET}")
    print(
        f"{CYAN} Target: {BOLD}{target.upper()}{RESET} "
        f"{CYAN}| Base URL: {base_url or 'In-Process ASGI'}{RESET}"
    )
    print(f"{BOLD}{CYAN}{sep_line}{RESET}\n")

    overall_total = 0

    overall_passed = 0
    overall_failed = 0
    failures: List[Tuple[str, str, str]] = []
    tier_stats: Dict[int, Dict[str, int]] = {}

    start_time = time.time()

    for tier_num, tier_title, module in tiers:
        if tier_filter != "all" and str(tier_num) != str(tier_filter):
            continue

        test_list = discover_tier_tests(module)
        tier_stats[tier_num] = {"total": len(test_list), "passed": 0, "failed": 0}

        print(f"{BOLD}{BLUE}▶ {tier_title}{RESET} ({len(test_list)} tests)")
        print(f"{BLUE}{dash_line}{RESET}")

        if list_only:
            for name, _, doc in test_list:
                print(f"  • {BOLD}{name}{RESET}: {doc}")
            print()
            continue

        for name, func, doc in test_list:
            overall_total += 1
            t_start = time.time()
            try:
                func(client=client)
                t_duration = (time.time() - t_start) * 1000
                tier_stats[tier_num]["passed"] += 1
                overall_passed += 1
                status_str = f"{GREEN}[PASS]{RESET}"
                if verbose:
                    print(f"  {status_str} {BOLD}{name}{RESET} ({t_duration:.1f}ms) — {doc}")
                else:
                    print(f"  {status_str} {name} ({t_duration:.1f}ms)")
            except Exception as ex:
                t_duration = (time.time() - t_start) * 1000
                tier_stats[tier_num]["failed"] += 1
                overall_failed += 1
                status_str = f"{RED}[FAIL]{RESET}"
                failures.append((tier_title, name, str(ex)))
                print(
                    f"  {status_str} {BOLD}{name}{RESET} "
                    f"({t_duration:.1f}ms) — {RED}{ex}{RESET}"
                )

        print()

    duration = time.time() - start_time

    if list_only:
        total_discovered = sum(len(discover_tier_tests(m)) for _, _, m in tiers)
        print(f"{BOLD}Total Discovered Tests: {total_discovered}{RESET}\n")
        return 0

    # Summary Table
    print(f"{BOLD}{CYAN}{sep_line}{RESET}")
    print(f"{BOLD}{CYAN} E2E Test Suite Execution Summary{RESET}")
    print(f"{BOLD}{CYAN}{sep_line}{RESET}")
    print(f"{'Tier':<45} | {'Total':<8} | {'Passed':<8} | {'Failed':<8} | {'Rate':<8}")
    print(f"{'-' * 45}-+-{'-' * 8}-+-{'-' * 8}-+-{'-' * 8}-+-{'-' * 8}")

    for t_num, stats in tier_stats.items():
        t_name = f"Tier {t_num}"
        pass_rate = (stats['passed'] / stats['total'] * 100) if stats['total'] > 0 else 0
        rate_str = (
            f"{GREEN}{pass_rate:.1f}%{RESET}"
            if pass_rate == 100
            else f"{RED}{pass_rate:.1f}%{RESET}"
        )
        print(
            f"{t_name:<45} | {stats['total']:<8} | "
            f"{stats['passed']:<8} | {stats['failed']:<8} | {rate_str:<8}"
        )

    print(f"{'-' * 45}-+-{'-' * 8}-+-{'-' * 8}-+-{'-' * 8}-+-{'-' * 8}")
    total_rate = (overall_passed / overall_total * 100) if overall_total > 0 else 0
    total_rate_str = (
        f"{GREEN}{total_rate:.1f}%{RESET}"
        if total_rate == 100
        else f"{RED}{total_rate:.1f}%{RESET}"
    )
    print(
        f"{BOLD}{'OVERALL TOTAL':<45} | {overall_total:<8} | "
        f"{overall_passed:<8} | {overall_failed:<8} | {total_rate_str:<8}{RESET}"
    )
    print(f"{CYAN}Duration: {duration:.2f} seconds{RESET}\n")

    if failures:
        print(f"{BOLD}{RED}FAILURES DETECTED ({len(failures)}):{RESET}")
        for tier_title, name, err in failures:
            print(f"  • [{tier_title}] {BOLD}{name}{RESET}: {RED}{err}{RESET}")
        print()
        return 1

    print(
        f"{BOLD}{GREEN}✓ ALL {overall_passed} E2E TESTS PASSED SUCCESSFULLY "
        f"(100% PASS RATE)!{RESET}\n"
    )
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Hindi Wikipedia Reference Fixer E2E Test Runner"
    )
    parser.add_argument(
        "--target",
        choices=["contract", "app", "live"],
        default="contract",
        help="Target backend: 'contract' (oracle), 'app' (FastAPI), or 'live' (HTTP)",
    )
    parser.add_argument(
        "--url",
        default=None,
        help="Base URL for live target (e.g. http://localhost:8000)",
    )
    parser.add_argument(
        "--tier",
        choices=["1", "2", "3", "4", "all"],
        default="all",
        help="Filter execution by specific Tier (1-4 or all)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable detailed verbose output with test descriptions",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all discovered test cases without executing",
    )

    args = parser.parse_args()
    target = "live" if args.url else args.target
    exit_code = run_test_suite(
        target=target,
        base_url=args.url,
        tier_filter=args.tier,
        verbose=args.verbose,
        list_only=args.list,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
