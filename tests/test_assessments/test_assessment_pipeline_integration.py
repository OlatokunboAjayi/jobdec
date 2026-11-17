"""Integration tests wiring the Assessment Door into Telegram Bot, CLI, and daily_run pipeline."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_orchestrator_cli_accepts_telegram_flags():
    from assessments.orchestrator import build_parser
    args = build_parser().parse_args(["--solve-pending", "--headless", "--dry-run"])
    assert args.solve_pending is True
    assert args.headless is True
    assert args.dry_run is True


def test_telegram_assessment_door_launch_and_panel(monkeypatch, tmp_path):
    import telegram_bot_service as tbs
    import candidate as CAND

    fake_inbox = tmp_path / "agents" / "cache" / "inbox.json"
    fake_inbox.parent.mkdir(parents=True, exist_ok=True)
    fake_inbox.write_text(
        json.dumps({
            "501": {
                "company": "TestQuant",
                "role": "Trading Intern",
                "kind": "assessment",
                "subject": "Online Assessment",
                "body": "Start test: https://app.codility.com/c/run/TEST-123",
            }
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(CAND, "DATA", tmp_path)
    monkeypatch.setattr(CAND, "APPLICATIONS", tmp_path / "applications.csv")

    text, kb = tbs.get_assessments_door_panel()
    assert "Assessment Door" in text
    assert "TestQuant" in text
    btn_callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "btn_run_assessments" in btn_callbacks

    launch_res = tbs.launch_assessment_solver_from_telegram(dry_run=True)
    assert launch_res["ok"] is True
    assert "-m assessments --solve-pending --headless" in launch_res["cmd"]


def test_daily_run_assessment_step_dry_run(monkeypatch, tmp_path):
    import daily_run
    import candidate as CAND

    monkeypatch.setattr(CAND, "DATA", tmp_path)
    monkeypatch.setattr(CAND, "APPLICATIONS", tmp_path / "applications.csv")
    out = daily_run.run_assessments_step(solve=False, max_tasks=3)
    assert isinstance(out, list)
