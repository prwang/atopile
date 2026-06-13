# This file is part of the faebryk project
# SPDX-License-Identifier: MIT
"""
Resilient EasyEDA API client (BACKLOG D1).

easyeda.com's component API sits behind a CloudFront WAF with two independent
rules that the upstream ``easyeda2kicad.EasyedaApi`` walks straight into:

1. A User-Agent allow/deny list. ``Mozilla/*``, ``python-requests/*`` and the
   library's own hardcoded ``User-Agent: easyeda2kicad v<version>`` are all
   403'd *deterministically, at any request rate*; plain-CLI UAs (``curl/*``,
   node) are let through. So the upstream client is blocked by its own identity,
   not by throttling — and because it calls ``r.json()`` without checking the
   status, the 403 surfaces as ``JSONDecodeError: Expecting value: line 1
   column 1`` (historically misread as "empty JSON / rate limit").

2. A per-IP rate/reputation rule. Bursts (a cold build fetches every uncached
   part back-to-back, with no client-side spacing whatsoever) trip CloudFront,
   after which even an allowed UA gets a 403 whose body is the
   ``Request blocked / too much traffic`` HTML page.

This subclass fixes both: it sends a WAF-allowlisted User-Agent, and it routes
every GET through full-jitter exponential backoff that treats a WAF block (403,
or an HTML body where JSON was expected) as retryable. A genuine 200 ``{"success":
false}`` (part not found) is *not* a WAF block and is returned immediately — we
don't hammer the API for parts that don't exist.

The 1-day on-disk cache (``part_lifecycle.EasyEDA_API``) still means warm builds
make zero API calls; this only governs the cold-fetch path.
"""

import logging
import random
import time
from collections.abc import Callable

import httpx
from easyeda2kicad.easyeda import easyeda_api as _ee

logger = logging.getLogger(__name__)

# A User-Agent the EasyEDA CloudFront WAF allowlists. Browser-impersonating and
# known-scraper UAs are denied; a plain CLI UA passes (measured 2026-06-13).
ALLOWED_USER_AGENT = "curl/8.5.0"


class ResilientEasyedaApi(_ee.EasyedaApi):
    """``EasyedaApi`` with a WAF-allowlisted User-Agent and jittered
    exponential-backoff retry on CloudFront blocks. Drop-in for the upstream
    class (same method surface)."""

    MAX_ATTEMPTS = 5
    BASE_DELAY_S = 0.5
    MAX_DELAY_S = 8.0
    TIMEOUT_S = 30.0

    def __init__(self) -> None:
        super().__init__()
        # the deterministic fix: identify as a plain CLI client, not as
        # easyeda2kicad (which the WAF denylists). All three upstream methods
        # read self.headers["User-Agent"], so this covers JSON + 3D-model GETs.
        self.headers["User-Agent"] = ALLOWED_USER_AGENT

    # -- retry core --------------------------------------------------------

    def _backoff_delay(self, attempt: int) -> float:
        """Full-jitter exponential backoff: uniform(0, min(cap, base*2**n))."""
        ceiling = min(self.MAX_DELAY_S, self.BASE_DELAY_S * (2**attempt))
        return random.uniform(0.0, ceiling)

    @staticmethod
    def _is_waf_block(r: httpx.Response) -> bool:
        """A CloudFront block: a 403, or any non-JSON (HTML) body where the API
        normally returns JSON. A legit 200 application/json (incl. success:false)
        is not a block."""
        if r.status_code == 403:
            return True
        ctype = r.headers.get("content-type", "").lower()
        return "html" in ctype

    def _make_client(self) -> httpx.Client:
        # seam for tests: set self._transport to an httpx.MockTransport to avoid
        # real network. Production uses the truststore SSL context as upstream.
        transport = getattr(self, "_transport", None)
        if transport is not None:
            return httpx.Client(transport=transport, timeout=self.TIMEOUT_S)
        return httpx.Client(verify=self._ssl_context, timeout=self.TIMEOUT_S)

    def _get_with_retry(self, url: str, headers: dict) -> httpx.Response:
        """GET with full-jitter exponential backoff while CloudFront blocks us.
        Returns the last response either way (callers keep their existing
        success/empty handling); transport errors are retried too."""
        last: httpx.Response | None = None
        for attempt in range(self.MAX_ATTEMPTS):
            try:
                with self._make_client() as client:
                    last = client.get(url=url, headers=headers)
            except httpx.HTTPError as e:
                logger.warning(f"EasyEDA GET {url} transport error: {e}")
                last = None
            else:
                if not self._is_waf_block(last):
                    return last
                logger.warning(
                    f"EasyEDA WAF blocked GET {url} "
                    f"(HTTP {last.status_code}); attempt {attempt + 1}"
                    f"/{self.MAX_ATTEMPTS}"
                )
            if attempt < self.MAX_ATTEMPTS - 1:
                time.sleep(self._backoff_delay(attempt))
        if last is None:
            raise httpx.HTTPError(f"EasyEDA GET {url} failed after retries")
        return last

    # -- overrides (route the upstream methods through the resilient GET) ---

    def get_info_from_easyeda_api(self, lcsc_id: str) -> dict:
        r = self._get_with_retry(
            url=_ee.API_ENDPOINT.format(lcsc_id=lcsc_id), headers=self.headers
        )
        if self._is_waf_block(r):
            # exhausted retries against the WAF — behave like the upstream
            # "no data" path (caller raises a clean LCSC_NoDataException)
            logger.error(f"EasyEDA still blocked for {lcsc_id} after retries")
            return {}
        api_response = r.json()
        if not api_response or (
            "code" in api_response and api_response.get("success") is False
        ):
            logger.debug(f"{api_response}")
            return {}
        return api_response

    def _get_model_content(self, url: str, what: str):
        r = self._get_with_retry(
            url=url, headers={"User-Agent": self.headers["User-Agent"]}
        )
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError:
            logger.error(f"No {what} found at {url} on easyeda")
            return None
        return r

    def get_raw_3d_model_obj(self, uuid: str) -> str | None:
        r = self._get_model_content(
            _ee.ENDPOINT_3D_MODEL.format(uuid=uuid), "raw 3D model"
        )
        return r.content.decode() if r is not None else None

    def get_step_3d_model(self, uuid: str) -> bytes | None:
        r = self._get_model_content(
            _ee.ENDPOINT_3D_MODEL_STEP.format(uuid=uuid), "step 3D model"
        )
        return r.content if r is not None else None


def make_easyeda_api(
    factory: Callable[[], _ee.EasyedaApi] = ResilientEasyedaApi,
) -> _ee.EasyedaApi:
    """Single construction point for the EasyEDA client so call sites don't each
    decide which implementation to use."""
    return factory()
