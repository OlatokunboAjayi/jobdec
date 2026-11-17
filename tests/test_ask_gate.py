"""2026-11-16: the owner was asked ~630 questions the logic could answer (country, school, reworded questions
they had already answered on Telegram). Every layer must be checked before a question reaches Telegram."""
from agents import options as OPTS
import telegram_notify as TN


def test_fact_questions_use_the_candidates_own_facts(monkeypatch):
    monkeypatch.setenv("CANDIDATE_COUNTRY", "Testland")
    cat, rule = OPTS.fact_rule("Country*", lambda q: None)
    assert cat == "country" and rule["a"] == "Testland"


def test_question_the_logic_answers_is_not_sent(monkeypatch):
    sent = []
    monkeypatch.setattr(TN, "known_by_logic", lambda q, o=None, c="": True)
    monkeypatch.setattr(TN, "send_message", lambda *a, **k: sent.append(a) or 1)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    assert TN.ask_question_interactive("What country do you live in?") is False and not sent


def test_telegram_answers_teach_the_reworded_question_matcher(monkeypatch):
    from agents import semantic
    import candidate
    monkeypatch.setattr(candidate, "profile", lambda: {"rules": [
        {"q": r"have\ you\ worked\ for\ us\ before", "a": "No", "src": "telegram_user"}]})
    monkeypatch.setattr(semantic, "AUTOFILL", semantic.AUTOFILL / "no-records-here")
    assert ("have you worked for us before", "No") in semantic._known_answers()


def test_outbox_skips_old_applications_and_code_fields(monkeypatch, tmp_path):
    import json, time
    out = tmp_path / "unanswered.json"
    now = time.strftime("%Y-%m-%dT%H:%M")
    out.write_text(json.dumps([
        {"url": "https://old.example/job", "when": "2026-10-26T10:00", "questions": [{"label": "Old question?"}]},
        {"url": "https://new.example/job", "when": now, "questions": [{"label": "New question?"}]}]), encoding="utf-8")
    monkeypatch.setattr(TN, "OUTBOX_QUESTIONS", out)
    monkeypatch.setattr(TN, "OUTBOX_STATE", tmp_path / "state.json")
    seen = []
    monkeypatch.setattr(TN, "ask_question_interactive", lambda q, o=None, c="": seen.append(q) or False)
    TN.process_outbox()
    assert seen == ["New question?"]
    assert TN.NEVER_ASK.search("Enter verification code digit 2 of six.")
