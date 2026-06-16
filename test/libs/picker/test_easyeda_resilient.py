# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""
Regression tests for the resilient EasyEDA client (BACKLOG D1).

Pins the two fixes against the CloudFront WAF, using a stubbed httpx transport
(no real network):
  1. requests carry a WAF-allowlisted User-Agent, NOT the upstream
     `easyeda2kicad v<version>` that gets 403'd deterministically;
  2. a WAF block (403, or HTML where JSON is expected) is retried with backoff,
     while a genuine 200 success:false is returned immediately (no hammering);
  3. pacing is proactive: every GET (even the first, even on the happy path) is
     preceded by a jittered initial delay so a cold serial fetch stream is spaced
     from the start, not only after the first 403.
"""

import httpx
import pytest

import faebryk.library._F as F  # noqa: F401  # prevents a circular import
from faebryk.libs.picker import easyeda_resilient as er

# the CloudFront block body the WAF actually returns (abridged)
_WAF_BLOCK_HTML = (
    '<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN">'
    "<HTML><HEAD><TITLE>ERROR: The request could not be satisfied</TITLE></HEAD>"
    "<BODY><H1>403 ERROR</H1>Request blocked.</BODY></HTML>"
)
_GOOD_JSON = {"success": True, "code": 0, "result": {"title": "PART", "dataStr": {}}}


@pytest.fixture(autouse=True)
def _no_real_sleep_or_jitter(monkeypatch):
    """Keep tests fast and deterministic: no real backoff sleep, zero jitter."""
    slept: list[float] = []
    monkeypatch.setattr(er.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(er.random, "uniform", lambda a, b: b)  # max of the window
    return slept


def _api_with_responses(responses: list[httpx.Response]):
    """Build a ResilientEasyedaApi whose transport replays `responses` in order
    and records every request (so we can assert the User-Agent and call count)."""
    seen: list[httpx.Request] = []
    it = iter(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return next(it)

    api = er.ResilientEasyedaApi()
    api._transport = httpx.MockTransport(handler)
    return api, seen


def _json_resp(payload: dict) -> httpx.Response:
    return httpx.Response(200, json=payload)


def _waf_resp() -> httpx.Response:
    return httpx.Response(
        403, text=_WAF_BLOCK_HTML, headers={"content-type": "text/html"}
    )


def test_uses_allowlisted_user_agent_not_easyeda2kicad():
    api, seen = _api_with_responses([_json_resp(_GOOD_JSON)])
    api.get_cad_data_of_component(lcsc_id="C125116")

    ua = seen[0].headers["user-agent"]
    assert ua == er.ALLOWED_USER_AGENT
    assert "easyeda2kicad" not in ua  # the upstream UA the WAF denylists


def test_retries_waf_block_then_succeeds(_no_real_sleep_or_jitter):
    api, seen = _api_with_responses(
        [_waf_resp(), _waf_resp(), _json_resp(_GOOD_JSON)]
    )
    data = api.get_cad_data_of_component(lcsc_id="C1")

    assert data == _GOOD_JSON["result"]
    assert len(seen) == 3  # two blocks + one success
    # proactive initial jitter (ceiling INITIAL_JITTER_S) before the first GET,
    # then full-jitter exponential backoff (ceilings 0.5, 1.0) before each retry.
    # uniform is monkeypatched to return the window's max, so we see the ceilings.
    initial = er.ResilientEasyedaApi.INITIAL_JITTER_S
    assert _no_real_sleep_or_jitter == [initial, 0.5, 1.0]


def test_happy_path_is_paced_proactively(_no_real_sleep_or_jitter):
    """Even a single successful GET (no WAF block) is preceded by the proactive
    initial jitter — that's what spaces a cold serial burst from the start."""
    api, seen = _api_with_responses([_json_resp(_GOOD_JSON)])
    api.get_cad_data_of_component(lcsc_id="C1")

    assert len(seen) == 1  # no retries
    # exactly one sleep: the proactive initial jitter, no reactive backoff
    assert _no_real_sleep_or_jitter == [er.ResilientEasyedaApi.INITIAL_JITTER_S]


def test_genuine_not_found_is_not_retried():
    """A 200 success:false is a real 'no such part', not a WAF block — return
    empty immediately, do not hammer the API."""
    api, seen = _api_with_responses(
        [_json_resp({"success": False, "code": 1, "result": None})]
    )
    data = api.get_cad_data_of_component(lcsc_id="C0")

    assert data == {}
    assert len(seen) == 1  # no retries


def test_exhausted_retries_return_empty_not_jsondecodeerror():
    """When the WAF blocks every attempt, surface the upstream 'no data'
    contract ({}), not a JSONDecodeError on the HTML body."""
    api, seen = _api_with_responses([_waf_resp()] * er.ResilientEasyedaApi.MAX_ATTEMPTS)
    data = api.get_cad_data_of_component(lcsc_id="C2")

    assert data == {}
    assert len(seen) == er.ResilientEasyedaApi.MAX_ATTEMPTS


def test_transport_error_is_retried(_no_real_sleep_or_jitter):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 2:
            raise httpx.ConnectError("boom")
        return _json_resp(_GOOD_JSON)

    api = er.ResilientEasyedaApi()
    api._transport = httpx.MockTransport(handler)
    data = api.get_cad_data_of_component(lcsc_id="C3")

    assert data == _GOOD_JSON["result"]
    assert calls["n"] == 2
