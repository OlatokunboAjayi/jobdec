"""Pytest fixtures for the Assessment Door E2E and mock portal suite."""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Generator

import pytest
import requests
import uvicorn

from assessments.browser import ANTI_BLUR_INIT_SCRIPT


@pytest.fixture(autouse=True)
def _assessment_test_env(monkeypatch):
    monkeypatch.setenv("ASSESSMENT_TEST_MODE", "true")
    monkeypatch.setenv("LLM_PROVIDER", "stub")
    monkeypatch.setenv("LLM_FALLBACK_ENABLED", "true")
    monkeypatch.setenv("DAILY_BUDGET_USD", "100.0")
    monkeypatch.setenv("CANDIDATE_NAME", "Test Candidate")
    monkeypatch.setenv("CANDIDATE_UNIVERSITY", "Test University")
    monkeypatch.setenv("CANDIDATE_DEGREE", "B.S. Computer Science")
    monkeypatch.setenv("CANDIDATE_GPA", "3.8")


@pytest.fixture(scope="module")
def mock_portal_server() -> Generator[str, None, None]:
    from tests.mock_portal.server import app

    host = "127.0.0.1"
    port = int(os.getenv("MOCK_PORTAL_PORT", "8898"))
    base_url = f"http://{host}:{port}"

    try:
        resp = requests.get(f"{base_url}/health", timeout=0.8)
        if resp.status_code == 200 and resp.json().get("status") == "ok":
            yield base_url
            return
    except Exception:
        pass

    config = uvicorn.Config(app=app, host=host, port=port, log_level="warning", loop="asyncio")
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True, name="MockPortalServerThread")
    t.start()

    start_time = time.time()
    while time.time() - start_time < 10.0:
        try:
            r = requests.get(f"{base_url}/health", timeout=0.5)
            if r.status_code == 200 and r.json().get("status") == "ok":
                break
        except Exception:
            time.sleep(0.1)
    else:
        server.should_exit = True
        raise RuntimeError(f"Mock assessment server failed to start on {base_url}")

    yield base_url

    server.should_exit = True
    try:
        requests.get(f"{base_url}/health", timeout=0.5)
    except Exception:
        pass
    t.join(timeout=2.0)


@pytest.fixture(scope="session")
def stub_provider():
    from tests.mock_portal.stub_provider import StubLLMProvider
    return StubLLMProvider()


@pytest.fixture(autouse=True)
def reset_stub_provider(stub_provider):
    stub_provider.reset()
    yield stub_provider
    stub_provider.reset()


@pytest.fixture(scope="module")
def playwright_instance():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="module")
def browser_context(playwright_instance, mock_portal_server):
    browser = playwright_instance.chromium.launch(
        headless=True,
        args=[
            "--use-fake-ui-for-media-stream",
            "--use-fake-device-for-media-stream",
            "--disable-blink-features=AutomationControlled",
        ],
    )
    context = browser.new_context(
        base_url=mock_portal_server,
        viewport={"width": 1280, "height": 800},
        permissions=["camera", "microphone"],
        ignore_https_errors=True,
    )
    context.add_init_script(ANTI_BLUR_INIT_SCRIPT)
    yield context
    context.close()
    browser.close()


@pytest.fixture
def assessment_page(browser_context) -> Generator[Any, None, None]:
    page = browser_context.new_page()
    yield page
    page.close()
