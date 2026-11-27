"""Fixtures for the upgrade test: a real Home Assistant and fake controllers.

Runs with pytest-homeassistant-custom-component, separately from tests/
(which stub Home Assistant out). See run_upgrade_test.sh.
"""
import os
import sys
import types
from pathlib import Path

import pytest

# Each phase runs in the directory holding the custom_components it tests: the
# previous release for the old phase, the working tree for the new one. Point
# Home Assistant's loader at exactly that directory, so no other
# custom_components package on sys.path is picked up instead.
_components = types.ModuleType("custom_components")
_components.__path__ = [str(Path(os.getcwd()) / "custom_components")]
sys.modules["custom_components"] = _components
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

sys.path.insert(0, str(Path(__file__).parent))
from fake_shc import CONTROLLERS, rpc_reply  # noqa: E402


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def fake_shcs(aioclient_mock):
    """Both controllers: log-in by GET, everything else by JSON-RPC POST."""
    for url in CONTROLLERS:
        aioclient_mock.get(url, headers={"Set-Cookie": "JSESSIONID=fakeEnd; Path=/"}, text="ok")

        async def handler(method, request_url, data, url=url):
            return AiohttpClientMockResponse(method, request_url, json=rpc_reply(url, data))

        aioclient_mock.post(url + "/remote/json-rpc", side_effect=handler)
    return aioclient_mock
