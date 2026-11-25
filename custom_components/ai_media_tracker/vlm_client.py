"""VLM provider abstraction for AI Media Tracker.

Supports a local Ollama server and the Google Gemini API. The Gemini path
rotates across several models because the free tier meters each model
separately, so the usable budget is the sum of the per-model buckets:

    Flash-Lite  gemini-3.5-flash-lite   15 RPM /  500 RPD   <- primary
                gemini-3.1-flash-lite   15 RPM /  500 RPD
                gemini-2.5-flash-lite   10 RPM /   20 RPD   (burst only)
    Flash       gemini-3.5-flash         5 RPM /   20 RPD   <- escalation
                gemini-3.6-flash         5 RPM /   20 RPD
                gemini-3.7-flash         5 RPM /   20 RPD
                gemini-3-flash-preview   5 RPM /   20 RPD
                gemini-2.5-flash         5 RPM /   20 RPD

Measured on real captured frames (13-frame set, integration's own prompts):
Flash-Lite answers in ~1.1s at 100% field accuracy; the local qwen3-vl:4b
takes ~27s at 88% and hallucinates titles. Escalation to the Flash tier is
reserved for low-confidence results, since Flash-Lite left no measured
headroom to improve on.

Daily counters are persisted so a Home Assistant restart cannot silently
overrun the quota. Google resets the free-tier RPD at midnight Pacific.
"""
import asyncio
import base64
import json
import logging
import random
import re
import time
from datetime import datetime, timedelta, timezone

from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY = "ai_media_tracker.vlm_quota"

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"

PROVIDER_OLLAMA = "ollama"
PROVIDER_GEMINI = "gemini"

# tier -> ordered model list. Earlier entries are preferred.
# (model_id, requests_per_minute, requests_per_day)
TIER_PRIMARY = "primary"
TIER_ESCALATE = "escalate"
TIER_OVERFLOW = "overflow"

GEMINI_TIERS = {
    TIER_PRIMARY: [
        ("gemini-3.5-flash-lite", 15, 500),
        ("gemini-3.1-flash-lite", 15, 500),
        ("gemini-2.5-flash-lite", 10, 20),
    ],
    TIER_ESCALATE: [
        ("gemini-3.5-flash", 5, 20),
        ("gemini-3.6-flash", 5, 20),
        ("gemini-3.7-flash", 5, 20),
        ("gemini-3-flash-preview", 5, 20),
        ("gemini-2.5-flash", 5, 20),
    ],
    TIER_OVERFLOW: [
        ("gemma-4-26b-a4b-it", 30, 14400),
        ("gemma-4-31b-it", 30, 14400),
    ],
}

SAFETY_SETTINGS = [
    {"category": c, "threshold": "BLOCK_NONE"}
    for c in (
        "HARM_CATEGORY_HARASSMENT",
        "HARM_CATEGORY_HATE_SPEECH",
        "HARM_CATEGORY_SEXUALLY_EXPLICIT",
        "HARM_CATEGORY_DANGEROUS_CONTENT",
    )
]

# Titles that mean "the model could not identify this", used to decide whether
# a primary-tier answer is weak enough to be worth escalating.
LOW_CONFIDENCE_TITLES = {"", "browsing...", "loading info...", "unknown", "none", "null"}


def extract_json(reply):
    """Pull the largest parseable JSON object out of a model reply.

    Scans for balanced braces rather than using a greedy ``\\{.*\\}`` regex.
    Some models (Gemma in particular) emit reasoning prose containing braces
    before the real JSON; a greedy match spans from a brace in the prose to
    the final brace and fails to parse, silently dropping a good result.
    """
    if not reply:
        return None
    if "`" in reply:
        reply = re.sub(r"`{3}(?:json)?", "", reply)

    candidates = []
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for i, ch in enumerate(reply):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start >= 0:
                candidates.append(reply[start:i + 1])

    for blob in sorted(candidates, key=len, reverse=True):
        try:
            parsed = json.loads(blob)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict) and parsed:
            return parsed
    return None


def is_low_confidence(result):
    """True when a VOD-style result did not actually identify anything."""
    if not isinstance(result, dict) or not result:
        return True
    if result.get("sports_info"):
        return False
    title = str(result.get("title") or "").strip().lower()
    return title in LOW_CONFIDENCE_TITLES


def _pacific_day():
    """Return today's date string in US Pacific, where Google resets RPD.

    Uses a fixed -08:00/-07:00 approximation rather than a tz database lookup;
    an hour of drift twice a year only shifts when the counter rolls over.
    """
    now = datetime.now(timezone.utc)
    offset = -7 if 3 <= now.month <= 10 else -8
    return (now + timedelta(hours=offset)).strftime("%Y-%m-%d")


class ModelBudget:
    """Sliding-window RPM plus a persisted daily RPD counter for one model."""

    def __init__(self, model, rpm, rpd, used_today=0, day=None):
        self.model = model
        self.rpm = rpm
        self.rpd = rpd
        self._recent = []
        self.used_today = used_today
        self.day = day or _pacific_day()
        self.cooldown_until = 0.0

    def _roll_day(self):
        today = _pacific_day()
        if today != self.day:
            self.day = today
            self.used_today = 0

    def available(self):
        """True when this model can take a request right now."""
        self._roll_day()
        now = time.monotonic()
        if now < self.cooldown_until:
            return False
        if self.used_today >= self.rpd:
            return False
        self._recent = [t for t in self._recent if now - t < 60.0]
        return len(self._recent) < self.rpm

    def consume(self):
        self._roll_day()
        self._recent.append(time.monotonic())
        self.used_today += 1

    def penalise(self, seconds=60.0):
        """Park this model after a 429 so we stop hammering it."""
        self.cooldown_until = time.monotonic() + seconds

    def as_dict(self):
        return {"day": self.day, "used_today": self.used_today}


class VLMClient:
    """Base interface: turn (prompt, JPEG bytes) into a parsed dict."""

    async def analyze(self, prompt, image_bytes, timeout=60, tier=TIER_PRIMARY):
        raise NotImplementedError

    async def preload(self):
        return None

    def describe(self):
        return "unconfigured"


class OllamaClient(VLMClient):
    """Local Ollama server. No rate limits, but slow and single-box."""

    def __init__(self, hass, url, model):
        self.hass = hass
        self.url = (url or "").rstrip("/")
        self.model = model

    def describe(self):
        return f"ollama {self.model} @ {self.url}"

    async def preload(self):
        try:
            session = async_get_clientsession(self.hass)
            await session.post(f"{self.url}/api/generate", json={"model": self.model})
        except Exception:
            pass

    async def analyze(self, prompt, image_bytes, timeout=60, tier=TIER_PRIMARY):
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        try:
            async with asyncio.timeout(timeout):
                session = async_get_clientsession(self.hass)
                response = await session.post(
                    f"{self.url}/api/generate",
                    json={"model": self.model, "prompt": prompt, "images": [b64],
                          "stream": False, "format": "json"},
                )
                if response.status != 200:
                    _LOGGER.warning("Ollama returned HTTP %s", response.status)
                    return None
                payload = json.loads(await response.text())
        except asyncio.TimeoutError:
            _LOGGER.warning("Ollama request timed out after %ss", timeout)
            return None
        except Exception as err:
            _LOGGER.error("Ollama request failed: %s", err)
            return None

        reply = (payload.get("response") or "").strip() or (payload.get("thinking") or "").strip()
        return extract_json(reply)


class GeminiClient(VLMClient):
    """Google Gemini API with per-model quota tracking and tier rotation."""

    def __init__(self, hass, api_key, store=None, allow_overflow=False):
        self.hass = hass
        self.api_key = api_key
        self._store = store
        self._allow_overflow = allow_overflow
        self._budgets = {}
        for tier, models in GEMINI_TIERS.items():
            for model, rpm, rpd in models:
                self._budgets[model] = ModelBudget(model, rpm, rpd)
        self._lock = asyncio.Lock()

    def describe(self):
        return "gemini api (flash-lite rotation)"

    async def async_load_quota(self):
        """Restore today's counters so a restart cannot overrun the daily cap."""
        if not self._store:
            return
        try:
            data = await self._store.async_load() or {}
        except Exception as err:
            _LOGGER.warning("Could not load VLM quota state: %s", err)
            return
        today = _pacific_day()
        for model, saved in (data.get("models") or {}).items():
            budget = self._budgets.get(model)
            if budget and saved.get("day") == today:
                budget.day = saved["day"]
                budget.used_today = int(saved.get("used_today", 0))
        _LOGGER.debug("Restored VLM quota counters for %s", today)

    async def _async_save_quota(self):
        if not self._store:
            return
        try:
            await self._store.async_save(
                {"models": {m: b.as_dict() for m, b in self._budgets.items()}}
            )
        except Exception as err:
            _LOGGER.debug("Could not persist VLM quota state: %s", err)

    def quota_report(self):
        """Human-readable remaining budget, for diagnostics."""
        lines = []
        for tier, models in GEMINI_TIERS.items():
            for model, _rpm, rpd in models:
                budget = self._budgets[model]
                lines.append(f"{tier}:{model} {budget.used_today}/{rpd}")
        return ", ".join(lines)

    def _pick_model(self, tier):
        for model, _rpm, _rpd in GEMINI_TIERS.get(tier, []):
            if self._budgets[model].available():
                return model
        return None

    async def _request(self, model, prompt, image_bytes, timeout):
        """One API call. Returns (parsed_dict_or_None, status_string)."""
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        body = {
            "contents": [{"parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": "image/jpeg", "data": b64}},
            ]}],
            "generationConfig": {"temperature": 0},
        }
        # Gemma on this API rejects responseMimeType and safetySettings.
        if not model.startswith("gemma"):
            body["generationConfig"]["responseMimeType"] = "application/json"
            body["safetySettings"] = SAFETY_SETTINGS

        url = f"{GEMINI_BASE}/models/{model}:generateContent?key={self.api_key}"
        try:
            async with asyncio.timeout(timeout):
                session = async_get_clientsession(self.hass)
                response = await session.post(url, json=body)
                text = await response.text()
        except asyncio.TimeoutError:
            return None, "timeout"
        except Exception as err:
            _LOGGER.debug("Gemini transport error on %s: %s", model, err)
            return None, "error"

        if response.status == 429:
            return None, "rate_limited"
        if response.status in (400, 403):
            _LOGGER.error("Gemini rejected the request on %s (HTTP %s): %s",
                          model, response.status, text[:300])
            return None, "rejected"
        if response.status != 200:
            _LOGGER.warning("Gemini HTTP %s on %s", response.status, model)
            return None, "error"

        try:
            payload = json.loads(text)
        except ValueError:
            return None, "error"

        candidates = payload.get("candidates") or []
        if not candidates:
            # Safety filtering returns no candidate at all. Treat as an empty
            # read; the caller's consensus vote simply skips this frame.
            feedback = payload.get("promptFeedback", {})
            _LOGGER.info("Gemini returned no candidate on %s (%s)", model, feedback)
            return None, "blocked"

        parts = candidates[0].get("content", {}).get("parts") or []
        reply = "".join(p.get("text", "") for p in parts).strip()
        return extract_json(reply), "ok"

    async def analyze(self, prompt, image_bytes, timeout=30, tier=TIER_PRIMARY):
        """Try each model in the tier until one answers, then fall back a tier."""
        tiers = [tier]
        if tier == TIER_PRIMARY and self._allow_overflow:
            tiers.append(TIER_OVERFLOW)

        for current in tiers:
            attempts = 0
            # Bounded so an exhausted tier cannot spin.
            while attempts < len(GEMINI_TIERS.get(current, [])):
                attempts += 1
                async with self._lock:
                    model = self._pick_model(current)
                    if model:
                        self._budgets[model].consume()
                if not model:
                    break

                # Overflow (Gemma) is much slower; give it room to answer.
                call_timeout = timeout * 3 if model.startswith("gemma") else timeout
                result, status = await self._request(model, prompt, image_bytes, call_timeout)

                if status == "ok":
                    await self._async_save_quota()
                    if result:
                        _LOGGER.debug("VLM answered via %s", model)
                    return result
                if status == "rate_limited":
                    # Google disagrees with our accounting; trust Google.
                    async with self._lock:
                        self._budgets[model].penalise(60.0)
                    _LOGGER.info("Gemini rate-limited %s, rotating", model)
                    await asyncio.sleep(random.uniform(0.1, 0.4))
                    continue
                if status in ("blocked", "rejected"):
                    return None
                # timeout / transport error: try the next model in the tier.

        _LOGGER.warning("No VLM capacity left (%s)", self.quota_report())
        return None


class TieredVLM:
    """What the integration talks to.

    Routes to the configured provider, escalates weak identifications to the
    Flash tier, and falls back to a local Ollama server when the cloud path is
    unavailable.
    """

    def __init__(self, hass, provider, gemini=None, ollama=None, escalate=True):
        self.hass = hass
        self.provider = provider
        self.gemini = gemini
        self.ollama = ollama
        self.escalate = escalate

    def describe(self):
        primary = self.gemini if self.provider == PROVIDER_GEMINI else self.ollama
        desc = primary.describe() if primary else "none"
        if self.provider == PROVIDER_GEMINI and self.ollama:
            desc += f" (fallback: {self.ollama.describe()})"
        return desc

    async def preload(self):
        if self.ollama:
            await self.ollama.preload()

    async def analyze(self, prompt, image_bytes, timeout=30, allow_escalation=False):
        """Run one analysis. Returns a parsed dict, or None."""
        if self.provider == PROVIDER_GEMINI and self.gemini:
            result = await self.gemini.analyze(prompt, image_bytes, timeout=timeout)

            if result is not None and not (allow_escalation and self.escalate
                                           and is_low_confidence(result)):
                return result

            if allow_escalation and self.escalate:
                # Flash-Lite either failed or could not identify the content.
                # The Flash tier is small (100 RPD) so it is only spent here.
                _LOGGER.debug("Escalating to Flash tier (weak primary result)")
                escalated = await self.gemini.analyze(
                    prompt, image_bytes, timeout=timeout, tier=TIER_ESCALATE)
                if escalated is not None:
                    return escalated

            if result is not None:
                return result
            if self.ollama:
                _LOGGER.info("Gemini unavailable, falling back to local Ollama")
                return await self.ollama.analyze(prompt, image_bytes, timeout=max(timeout, 90))
            return None

        if self.ollama:
            return await self.ollama.analyze(prompt, image_bytes, timeout=timeout)
        return None


async def async_build_client(hass, entry):
    """Construct the VLM client for a config entry."""
    opts = entry.options
    data = entry.data

    def _get(key, default=None):
        return opts.get(key, data.get(key, default))

    provider = _get("vlm_provider", PROVIDER_OLLAMA)
    ollama_url = _get("ollama_url", "http://localhost:11434")
    model_name = _get("model_name", "qwen3-vl:4b")
    api_key = _get("gemini_api_key", "")
    escalate = bool(_get("gemini_escalate", True))
    allow_overflow = bool(_get("gemini_allow_gemma", False))

    ollama = OllamaClient(hass, ollama_url, model_name) if ollama_url and model_name else None

    gemini = None
    if provider == PROVIDER_GEMINI:
        if not api_key:
            _LOGGER.error("Gemini selected but no API key configured; using Ollama")
            provider = PROVIDER_OLLAMA
        else:
            store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
            gemini = GeminiClient(hass, api_key, store=store, allow_overflow=allow_overflow)
            await gemini.async_load_quota()

    client = TieredVLM(hass, provider, gemini=gemini, ollama=ollama, escalate=escalate)
    _LOGGER.info("AI Media Tracker VLM provider: %s", client.describe())
    return client
