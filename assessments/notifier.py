"""
Multi-Channel Completion Notification Dispatcher & Audit Ledger (Features 19, 20, 21, R4).
Implements:
1. `dispatch_completion(event: AssessmentRunResult) -> Dict[str, bool]` (PROJECT.md contract)
2. Append-only structured JSONL audit ledger (`data/assessments.jsonl`) protected by `FileLock`
3. Application tracker stage sync (`applications.csv`: `OA/Assessment` -> `Completed OA / Awaiting Interview`)
4. Multi-channel alerts: Telegram Bot, Slack Webhook, Discord Webhook, Generic Webhook, and Gmail SMTP
"""

from __future__ import annotations

import csv
import json
import logging
import os
import pathlib
import smtplib
import urllib.request
from email.message import EmailMessage
from typing import Any, Dict, List, Optional

from assessments.contracts import AssessmentRunResult
import candidate as CAND
from tools.file_lock import FileLock

logger = logging.getLogger("assessments.notifier")


class AssessmentNotifier:
    """Dispatches assessment completion events across audit logs, CSV trackers, and messaging channels."""

    def __init__(
        self,
        ledger_path: Optional[pathlib.Path] = None,
        applications_csv: Optional[pathlib.Path] = None,
    ) -> None:
        self.ledger_path = ledger_path
        self.applications_csv = applications_csv

    def _resolve_ledger_path(self, override: Optional[pathlib.Path] = None) -> pathlib.Path:
        if override is not None:
            return override
        if self.ledger_path is not None:
            return self.ledger_path
        CAND.DATA.mkdir(parents=True, exist_ok=True)
        return CAND.DATA / "assessments.jsonl"

    def log_to_jsonl(
        self,
        event: AssessmentRunResult,
        ledger_path: Optional[pathlib.Path] = None,
    ) -> bool:
        """Appends a structured JSONL record for the assessment run with cross-process file locking."""
        target = self._resolve_ledger_path(ledger_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        platform_str = str(event.platform.value if hasattr(event.platform, "value") else event.platform)

        record = {
            "assessment_id": event.task_id,
            "task_id": event.task_id,
            "platform": platform_str,
            "company": event.company,
            "role": event.role,
            "assessment_url": event.assessment_url,
            "started_at": event.start_time,
            "completed_at": event.end_time,
            "duration_seconds": event.duration_seconds,
            "status": event.status,
            "score": event.score,
            "passed": event.passed,
            "proctoring_status": event.proctoring_status,
            "verification_token": event.verification_token,
            "questions_total": event.questions_total,
            "questions_answered": event.questions_answered,
            "llm_provider": event.llm_provider,
            "error": event.error_message,
        }
        try:
            with FileLock(target, timeout=5.0):
                with open(target, mode="a", encoding="utf-8") as f:
                    f.write(json.dumps(record) + "\n")
            return True
        except Exception as exc:
            logger.error(f"Failed writing to assessments.jsonl: {exc}")
            return False

    def sync_application_tracker(
        self,
        event: AssessmentRunResult,
        csv_path: Optional[pathlib.Path] = None,
    ) -> bool:
        """
        Synchronizes `applications.csv` by advancing matching company rows from `OA/Assessment`
        to `Completed OA / Awaiting Interview` upon assessment completion.
        """
        target_csv = csv_path or self.applications_csv
        if target_csv is None:
            # In sandboxed test mode, only modify explicitly provided CSV or sandboxed CSV
            if os.getenv("ASSESSMENT_TEST_MODE", "").lower() in ("true", "1", "yes"):
                jobdec_home = os.getenv("JOBDEC_HOME", "").strip()
                if jobdec_home:
                    candidate_csv = pathlib.Path(jobdec_home) / "applications.csv"
                    if candidate_csv.exists():
                        target_csv = candidate_csv
                    else:
                        return False
                else:
                    return False
            else:
                target_csv = CAND.APPLICATIONS

        if not target_csv or not target_csv.exists():
            return False

        if event.status not in ("COMPLETED", "PARTIAL"):
            return False

        updated = False
        try:
            with FileLock(target_csv, timeout=5.0):
                with open(target_csv, mode="r", encoding="utf-8", newline="") as f:
                    reader = csv.DictReader(f)
                    fieldnames = list(reader.fieldnames or [])
                    rows = list(reader)

                target_co = (event.company or "").strip().lower()
                for row in rows:
                    row_co = (row.get("Company") or "").strip().lower()
                    stage = (row.get("Stage") or "").strip()
                    if target_co and (row_co == target_co or target_co in row_co or row_co in target_co):
                        if "oa" in stage.lower() or "assessment" in stage.lower():
                            row["Stage"] = "Completed OA / Awaiting Interview"
                            prev_notes = (row.get("Notes") or "").strip()
                            note_suffix = f"Assessment {event.status} ({event.end_time[:10]})"
                            row["Notes"] = f"{prev_notes}; {note_suffix}".strip("; ")
                            updated = True

                if updated and fieldnames:
                    with open(target_csv, mode="w", encoding="utf-8", newline="") as f:
                        writer = csv.DictWriter(f, fieldnames=fieldnames)
                        writer.writeheader()
                        writer.writerows(rows)
            return updated
        except Exception as exc:
            logger.warning(f"Failed syncing applications.csv: {exc}")
            return False

    @staticmethod
    def format_summary_message(event: AssessmentRunResult) -> str:
        platform_str = str(event.platform.value if hasattr(event.platform, "value") else event.platform).upper()
        score_str = f"{event.score:.1f}%" if event.score is not None else "N/A"
        return (
            f"[Assessment {event.status}] {event.company} ({event.role}) | "
            f"Platform: {platform_str} | Answered: {event.questions_answered}/{event.questions_total} | "
            f"Score: {score_str} | Duration: {event.duration_seconds:.1f}s | "
            f"Proctoring: {event.proctoring_status}"
        )

    def send_telegram(self, event: AssessmentRunResult) -> bool:
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        if not token or not chat_id or token.startswith("mock_"):
            return False

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = json.dumps({
            "chat_id": chat_id,
            "text": self.format_summary_message(event),
        }).encode("utf-8")
        try:
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                return 200 <= resp.status < 300
        except Exception:
            return False

    def send_webhook(self, webhook_url: str, payload_dict: Dict[str, Any]) -> bool:
        clean_url = (webhook_url or "").strip()
        if not clean_url or clean_url.startswith("mock_"):
            return False
        data = json.dumps(payload_dict).encode("utf-8")
        try:
            req = urllib.request.Request(
                clean_url,
                data=data,
                headers={"Content-Type": "application/json", "User-Agent": "LinkedInOutreach-AssessmentNotifier/1.0"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                return 200 <= resp.status < 300
        except Exception:
            return False

    def send_email_smtp(self, event: AssessmentRunResult) -> bool:
        gmail_user = os.getenv("GMAIL_EMAIL", "").strip() or os.getenv("GMAIL_USER", "").strip()
        gmail_pass = os.getenv("GMAIL_APP_PASSWORD", "").strip()
        notify_to = os.getenv("ASSESSMENT_NOTIFY_EMAIL", "").strip() or gmail_user
        if not gmail_user or not gmail_pass or gmail_pass.startswith("mock_") or gmail_pass.startswith("your_"):
            return False
        if os.getenv("ASSESSMENT_TEST_MODE", "").lower() in ("true", "1", "yes"):
            return False

        msg = EmailMessage()
        msg["Subject"] = f"Assessment {event.status}: {event.company} ({event.platform})"
        msg["From"] = gmail_user
        msg["To"] = notify_to
        msg.set_content(self.format_summary_message(event))

        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=8.0) as smtp:
                smtp.login(gmail_user, gmail_pass)
                smtp.send_message(msg)
            return True
        except Exception:
            return False

    def dispatch_completion(
        self,
        event: AssessmentRunResult,
        ledger_path: Optional[pathlib.Path] = None,
        applications_csv: Optional[pathlib.Path] = None,
    ) -> Dict[str, bool]:
        """
        Primary notification contract (PROJECT.md R4):
        `notifier.dispatch_completion(event: AssessmentRunResult) -> Dict[str, bool]`
        """
        audit_ok = self.log_to_jsonl(event, ledger_path=ledger_path)
        tracker_ok = self.sync_application_tracker(event, csv_path=applications_csv)
        summary_text = self.format_summary_message(event)

        slack_url = os.getenv("SLACK_WEBHOOK_URL", "").strip()
        discord_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
        generic_url = os.getenv("ASSESSMENT_WEBHOOK_URL", "").strip()

        slack_ok = self.send_webhook(slack_url, {"text": summary_text}) if slack_url else False
        discord_ok = self.send_webhook(discord_url, {"content": summary_text}) if discord_url else False
        generic_ok = self.send_webhook(generic_url, event.model_dump()) if generic_url else False
        telegram_ok = self.send_telegram(event)
        email_ok = self.send_email_smtp(event)

        return {
            "audit_log": audit_ok,
            "tracker_sync": tracker_ok,
            "telegram": telegram_ok,
            "slack_webhook": slack_ok,
            "discord_webhook": discord_ok,
            "generic_webhook": generic_ok,
            "email_smtp": email_ok,
        }


_default_notifier: Optional[AssessmentNotifier] = None


def get_notifier() -> AssessmentNotifier:
    global _default_notifier
    if _default_notifier is None:
        _default_notifier = AssessmentNotifier()
    return _default_notifier


def dispatch_completion(
    event: AssessmentRunResult,
    ledger_path: Optional[pathlib.Path] = None,
    applications_csv: Optional[pathlib.Path] = None,
) -> Dict[str, bool]:
    """Module-level contract function: `notifier.dispatch_completion(event: AssessmentRunResult) -> Dict[str, bool]`."""
    return get_notifier().dispatch_completion(
        event,
        ledger_path=ledger_path,
        applications_csv=applications_csv,
    )
