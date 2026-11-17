"""
End-to-End Assessment Solver Orchestrator & CLI Entry Point (Feature 22, R4).
Coordinates:
1. Assessment task extraction (`extractor.extract_assessment_tasks`)
2. Browser automation or direct API execution (`browser.AssessmentBrowserDriver`)
3. Multi-modal question solving (`solver.AssessmentSolver`)
4. Completion notification and audit logging (`notifier.AssessmentNotifier`)
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from typing import Any, Dict, List, Optional, Union

from assessments.browser import AssessmentBrowserDriver
from assessments.contracts import (
    AssessmentRunResult,
    AssessmentTask,
)
from assessments.extractor import (
    classify_platform,
    extract_assessment_tasks,
    unwrap_redirect_url,
)
from assessments.notifier import AssessmentNotifier, dispatch_completion
from assessments.solver import AssessmentSolver, get_solver
from assessments.templates import get_behavioral_questions_catalog


class AssessmentOrchestrator:
    """
    End-to-end pipeline orchestrating Ingestion -> Browser Driver -> Solving Engine -> Notifier.
    """

    def __init__(
        self,
        solver: Optional[AssessmentSolver] = None,
        browser_driver: Optional[AssessmentBrowserDriver] = None,
        notifier: Optional[AssessmentNotifier] = None,
        headless: bool = True,
    ) -> None:
        self.solver = solver or get_solver()
        self.browser_driver = browser_driver or AssessmentBrowserDriver(solver=self.solver, headless=headless)
        self.notifier = notifier or AssessmentNotifier()

    def run_single_task(
        self,
        task: AssessmentTask,
        page: Optional[Any] = None,
        ledger_path: Optional[pathlib.Path] = None,
        applications_csv: Optional[pathlib.Path] = None,
    ) -> AssessmentRunResult:
        """Executes a single AssessmentTask and dispatches notifications & audit logs."""
        if page is not None:
            result = self.browser_driver.execute_assessment_on_page(page, task=task, url=task.canonical_url)
        else:
            result = self.browser_driver.run_task(task)

        self.notifier.dispatch_completion(
            result,
            ledger_path=ledger_path,
            applications_csv=applications_csv,
        )
        return result

    def run_url(
        self,
        url: str,
        company: str = "Target Company",
        role: str = "Software Engineer",
        page: Optional[Any] = None,
        ledger_path: Optional[pathlib.Path] = None,
        applications_csv: Optional[pathlib.Path] = None,
    ) -> AssessmentRunResult:
        """Wraps a URL into an AssessmentTask, solves it end-to-end, and logs completion."""
        canonical = unwrap_redirect_url(url)
        platform = classify_platform(canonical, context_text=f"{company} {role}")
        task = AssessmentTask(
            task_id=f"assess-{platform.value}",
            company=company,
            role=role,
            platform=platform,
            raw_url=url,
            canonical_url=canonical,
        )
        return self.run_single_task(
            task,
            page=page,
            ledger_path=ledger_path,
            applications_csv=applications_csv,
        )

    def run_pipeline(
        self,
        source: Union[str, pathlib.Path, List[Any], Dict[str, Any]] = "auto",
        dry_run: bool = False,
        max_tasks: Optional[int] = None,
        page: Optional[Any] = None,
        ledger_path: Optional[pathlib.Path] = None,
        applications_csv: Optional[pathlib.Path] = None,
    ) -> List[AssessmentRunResult]:
        """Extracts assessment tasks from `source` and executes them through the full pipeline."""
        tasks = extract_assessment_tasks(source)
        if max_tasks is not None:
            tasks = tasks[:max_tasks]

        if dry_run:
            return [
                AssessmentRunResult(
                    task_id=t.task_id,
                    status="PARTIAL",
                    company=t.company,
                    role=t.role,
                    platform=t.platform,
                    assessment_url=t.canonical_url,
                    questions_total=0,
                    questions_answered=0,
                )
                for t in tasks
            ]

        results: List[AssessmentRunResult] = []
        for task in tasks:
            res = self.run_single_task(
                task,
                page=page,
                ledger_path=ledger_path,
                applications_csv=applications_csv,
            )
            results.append(res)
        return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="solve-assessments",
        description="Autonomous Assessment Solver: ingests assessment links, solves behavioral/technical/math/logic questions, and dispatches notifications.",
    )
    parser.add_argument(
        "--source",
        default="auto",
        help="Ingestion source: 'auto', 'applications', 'inbox', file path, or direct URL (default: auto).",
    )
    parser.add_argument(
        "--url",
        default=None,
        help="Direct assessment URL to open and complete immediately.",
    )
    parser.add_argument(
        "--company",
        default="Target Company",
        help="Company name when running with --url.",
    )
    parser.add_argument(
        "--role",
        default="Software Engineer",
        help="Target role when running with --url.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Extract and list pending assessments without launching browser execution.",
    )
    parser.add_argument(
        "--max-tasks",
        type=int,
        default=None,
        help="Maximum number of assessments to process in batch mode.",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Launch visible browser window instead of headless mode.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser in headless mode (default).",
    )
    parser.add_argument(
        "--solve-pending",
        action="store_true",
        help="Solve all pending assessments discovered from --source (defaults to 'auto').",
    )
    parser.add_argument(
        "--list-behavioral-questions",
        action="store_true",
        help="Print the configurable behavioral archetype questions and .env keys.",
    )
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_behavioral_questions:
        catalog = get_behavioral_questions_catalog()
        print("=== Configurable Behavioral Assessment Questions (.env) ===")
        for idx, item in enumerate(catalog, start=1):
            print(f"{idx}. [{item['env_key']}]")
            print(f"   Question : {item['question']}")
            print(f"   Focus    : {item['default_focus']}\n")
        return 0

    orchestrator = AssessmentOrchestrator(headless=not args.headed)

    if args.url:
        if args.dry_run:
            tasks = extract_assessment_tasks(args.url)
            print(f"[Dry Run] Extracted {len(tasks)} assessment task(s) from URL: {args.url}")
            return 0
        res = orchestrator.run_url(args.url, company=args.company, role=args.role)
        print(AssessmentNotifier.format_summary_message(res))
        return 0 if res.status in ("COMPLETED", "PARTIAL") else 1

    results = orchestrator.run_pipeline(
        source=args.source,
        dry_run=args.dry_run,
        max_tasks=args.max_tasks,
    )
    print(f"Processed {len(results)} assessment task(s) (dry_run={args.dry_run}).")
    for r in results[:10]:
        print(" -", AssessmentNotifier.format_summary_message(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
