"""Pytest fixtures for mock_portal unit and integration tests."""
from __future__ import annotations

import os
import threading
import time
from typing import Generator

import pytest
import requests
import uvicorn
from fastapi.testclient import TestClient

from assessments.browser import ANTI_BLUR_INIT_SCRIPT


@pytest.fixture(scope="module")
def portal_client():
    from tests.mock_portal.server import app
    with TestClient(app) as client:
        yield client


@pytest.fixture(scope="module")
def stub_provider():
    from tests.mock_portal.stub_provider import StubLLMProvider
    return StubLLMProvider()


@pytest.fixture(autouse=True)
def reset_stub_provider(stub_provider):
    stub_provider.reset()
    yield stub_provider
    stub_provider.reset()


@pytest.fixture(scope="module")
def mock_portal_server() -> Generator[str, None, None]:
    from tests.mock_portal.server import app

    host = "127.0.0.1"
    port = int(os.getenv("MOCK_PORTAL_PORT", "8899"))
    base_url = f"http://{host}:{port}"

    config = uvicorn.Config(app=app, host=host, port=port, log_level="warning", loop="asyncio")
    server = uvicorn.Server(config)
    t = threading.Thread(target=server.run, daemon=True, name="MockPortalUnitThread")
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


@pytest.fixture(scope="module")
def browser_context(mock_portal_server):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"],
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
def assessment_page(browser_context):
    page = browser_context.new_page()
    yield page
    page.close()
