"""Makes the integration importable without a Home Assistant install.

If homeassistant or aiohttp are installed, the real modules are used. If not,
the few names the tests touch are stubbed, so the suite runs with plain pytest.
"""
import importlib
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "custom_components"))


def _ensure(name, **attrs):
    """Register a stub module for name, unless the real one imports."""
    try:
        importlib.import_module(name)
        return
    except ImportError:
        pass
    mod = types.ModuleType(name)
    mod.__dict__.update(attrs)
    sys.modules[name] = mod


class _ClientSession:
    pass


class _ClientConnectionError(Exception):
    pass


class _ConfigEntryError(Exception):
    pass


class _LightEntity:
    pass


class _BinarySensorEntity:
    pass


_ensure("aiohttp", ClientSession=_ClientSession,
        ClientConnectionError=_ClientConnectionError,
        BasicAuth=lambda **kwargs: None)
_ensure("aiofiles")
_ensure("homeassistant")
_ensure("homeassistant.exceptions",
        ConfigEntryAuthFailed=type("ConfigEntryAuthFailed", (_ConfigEntryError,), {}),
        ConfigEntryNotReady=type("ConfigEntryNotReady", (_ConfigEntryError,), {}))
_ensure("homeassistant.components")
_ensure("homeassistant.components.light", ATTR_BRIGHTNESS="brightness",
        LightEntity=_LightEntity,
        ColorMode=types.SimpleNamespace(BRIGHTNESS="brightness", ONOFF="onoff"))
_ensure("homeassistant.components.binary_sensor",
        BinarySensorEntity=_BinarySensorEntity,
        BinarySensorDeviceClass=types.SimpleNamespace(MOTION="motion"))


class _Flag(str):
    """A constant that also combines with | like the enum flags it stands in for."""

    def __or__(self, other):
        return self

    __ror__ = __or__


class _Names:
    """Any attribute is a constant named after itself, e.g. HVACMode.HEAT."""

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _Flag(name)


class _Entity:
    pass


class _CoordinatorEntity:
    def __init__(self, coordinator, *args, **kwargs):
        self.coordinator = coordinator


class _DataUpdateCoordinator:
    def __init__(self, hass, logger, name=None, update_interval=None, **kwargs):
        self.hass = hass
        self.name = name
        self.update_interval = update_interval
        self.data = None
        self.last_update_success = True

    async def async_refresh(self):
        pass


_ensure("homeassistant.core", callback=lambda func: func)
_ensure("homeassistant.const", PERCENTAGE="%", ATTR_TEMPERATURE="temperature",
        PRECISION_HALVES=0.5, UnitOfEnergy=_Names(), UnitOfPower=_Names(),
        UnitOfTemperature=_Names())
_ensure("homeassistant.helpers")
_ensure("homeassistant.helpers.entity", EntityCategory=_Names())
_ensure("homeassistant.helpers.aiohttp_client", async_get_clientsession=lambda hass: None)
_ensure("homeassistant.helpers.update_coordinator",
        CoordinatorEntity=_CoordinatorEntity,
        DataUpdateCoordinator=_DataUpdateCoordinator,
        UpdateFailed=type("UpdateFailed", (Exception,), {}))
_ensure("homeassistant.components.switch", SwitchEntity=_Entity)
_ensure("homeassistant.components.button", ButtonEntity=_Entity)
_ensure("homeassistant.components.cover", CoverEntity=_Entity, CoverEntityFeature=_Names())
_ensure("homeassistant.components.sensor", SensorEntity=_Entity,
        SensorDeviceClass=_Names(), SensorStateClass=_Names())
_ensure("homeassistant.components.climate", ClimateEntity=_Entity,
        HVACMode=_Names(), ClimateEntityFeature=_Names())

COMPONENT = REPO / "custom_components" / "xcomfort"

# Register the package without running its __init__.py, which needs the full
# Home Assistant coordinator machinery. Its submodules import normally.
_package = types.ModuleType("xcomfort")
_package.__path__ = [str(COMPONENT)]
sys.modules["xcomfort"] = _package

import xcomfort.xcomfortAPI as xcomfort_api  # noqa: E402


class FakeResponse:
    """The parts of an aiohttp response the integration reads."""

    def __init__(self, status=200, headers=None, body=None):
        self.status = status
        self.headers = headers or {}
        self._body = body or {}

    async def json(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """Returns canned responses, or raises ClientConnectionError when told to."""

    def __init__(self, get_response=None, post_error=False, post_bodies=None):
        self._get_response = get_response
        self._post_error = post_error
        self._post_bodies = list(post_bodies or [])
        self.posts = []

    def get(self, url, auth=None):
        return self._get_response

    def post(self, url, data=None, headers=None):
        self.posts.append(data)
        if self._post_error:
            raise xcomfort_api.aiohttp.ClientConnectionError("unreachable")
        return FakeResponse(body=self._post_bodies.pop(0))


@pytest.fixture
def make_api():
    def _make(session=None):
        return xcomfort_api.xcomfortAPI(session, "http://shc", "zone1", "user", "pw", 1)

    return _make
