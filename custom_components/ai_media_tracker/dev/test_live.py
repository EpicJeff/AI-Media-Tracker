"""Live end-to-end test of vlm_client.GeminiClient against the real API.

Exercises the exact aiohttp request path the integration will use, on real
captured frames, including escalation and the rate-limit rotation.
"""
import asyncio
import os
import sys
import time
import types

import aiohttp

_SESSION = None
for name in ("homeassistant", "homeassistant.helpers",
             "homeassistant.helpers.aiohttp_client", "homeassistant.helpers.storage"):
    sys.modules.setdefault(name, types.ModuleType(name))
sys.modules["homeassistant.helpers.aiohttp_client"].async_get_clientsession = lambda hass: _SESSION
sys.modules["homeassistant.helpers.storage"].Store = object

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import vlm_client as V

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bench import PROMPT_SPORTS, PROMPT_VOD, TESTDIR, optimize_image_for_vlm

KEY = os.environ.get("GEMINI_API_KEY", "")
if not KEY:
    _kf = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gemini_key.txt")
    KEY = open(_kf).read().strip().strip('"') if os.path.exists(_kf) else ""
if not KEY:
    raise SystemExit("Set GEMINI_API_KEY or place dev/gemini_key.txt (gitignored)")


def frame(name):
    return optimize_image_for_vlm(open(os.path.join(TESTDIR, name), "rb").read())


async def main():
    global _SESSION
    async with aiohttp.ClientSession() as session:
        _SESSION = session
        client = V.GeminiClient(None, KEY, store=None, allow_overflow=False)
        tiered = V.TieredVLM(None, V.PROVIDER_GEMINI, gemini=client, ollama=None)

        print("1. sports frame through the real client path")
        t0 = time.time()
        r = await tiered.analyze(PROMPT_SPORTS, frame("sports_test.jpg"), timeout=30)
        si = (r or {}).get("sports_info", {})
        print(f"   {time.time()-t0:.2f}s sport={si.get('sport')} "
              f"league={si.get('league')} state={si.get('game_state')}")
        assert si.get("sport") == "basketball", "expected basketball"

        print("2. VOD frame with escalation enabled")
        t0 = time.time()
        r = await tiered.analyze(PROMPT_VOD, frame("tvshow_test.jpg"),
                                 timeout=30, allow_escalation=True)
        print(f"   {time.time()-t0:.2f}s title={(r or {}).get('title')} "
              f"actors={(r or {}).get('show_info', {}).get('actors')}")
        assert "ncis" in str((r or {}).get("title", "")).lower(), "expected NCIS"

        print("3. trap frame (ticker advertises a different game)")
        r = await tiered.analyze(PROMPT_SPORTS, frame("sports_test_9.jpg"), timeout=30)
        blob = str(r).lower()
        print(f"   competitors={(r or {}).get('sports_info', {}).get('competitors')}")
        assert "tennessee" not in blob, "took the ticker bait"

        print("4. progress-bar prompt on a cropped frame")
        import io as _io
        from PIL import Image
        raw = open(os.path.join(TESTDIR, "sports_test.jpg"), "rb").read()
        img = Image.open(_io.BytesIO(raw))
        w, h = img.size
        buf = _io.BytesIO()
        img.crop((0, int(h * 0.66), w, h)).convert("RGB").save(buf, format="JPEG")
        prompt = ('Does this image contain a video progress bar or scrubber timeline? '
                  'Reply strictly with a JSON object: {"has_progress_bar": true} or '
                  '{"has_progress_bar": false}')
        t0 = time.time()
        r = await tiered.analyze(prompt, buf.getvalue(), timeout=30)
        print(f"   {time.time()-t0:.2f}s -> {r}")
        assert isinstance(r, dict) and "has_progress_bar" in r, "no progress-bar verdict"

        print("5. burst of 6 concurrent requests (rotation + 429 handling)")
        t0 = time.time()
        imgs = [frame(f) for f in ("sports_test.jpg", "sports_test_2.jpg",
                                   "sports_test_3.jpg", "sports_test_4.jpg",
                                   "sports_test_5.jpg", "sports_test_6.jpg")]
        rs = await asyncio.gather(*[tiered.analyze(PROMPT_SPORTS, i, timeout=30) for i in imgs])
        got = sum(1 for x in rs if x and x.get("sports_info", {}).get("sport"))
        print(f"   {time.time()-t0:.2f}s wall, {got}/6 returned a sport")
        print(f"   quota now: {client.quota_report()}")
        assert got >= 5, f"only {got}/6 survived the burst"

        print("\nall live checks passed")


asyncio.run(main())
