# Gemini API migration + codebase audit

Status as of **2026-08-17**. Written so this can be picked up cold.

---

## 1. What was decided and why

The integration ran vision inference against a local Ollama server
(`qwen3-vl:4b` at `http://localhost:11434`). It was slow enough to break
its own timeouts. We benchmarked alternatives on real captured frames and
switched the primary path to the Google Gemini API.

### Benchmark results

13 real frames from the local `www/ai-media-matrix/test` folder (not in this repository), using
the integration's own composite preprocessing and its own prompts verbatim.
Scoring is field-aware — it only counts fields the integration actually reads
(`title`, `sports_info.*`), not substring matches anywhere in the JSON.

| Model | Accuracy | Median latency | Notes |
|---|---|---|---|
| `qwen3-vl:4b` (local) | 28/32 — **88%** | **27.0s** | hallucinated "The Blacklist" for NCIS; took the ticker bait on the trap frame |
| `gemma-4-26b-a4b-it` (API) | not completed | **~30s** | correct content but wraps JSON in reasoning prose |
| `gemini-3.1-flash-lite` | 31/32 — **97%** | **1.32s** | called a promo title card "Browsing..." |
| `gemini-3.5-flash-lite` | 32/32 — **100%** | **1.12s** | primary choice |

Raw per-frame output is in `dev/results/*.json`.

### Gemma was rejected

It was the original candidate because of its huge daily allowance, but:

- **~30s per call** — no faster than the local model we were escaping, and
  structurally unable to feed a 20s sports poll.
- **~16 effective RPM.** Its 16K TPM ceiling binds well before its 30 RPM
  allowance (measured ~980 tokens/call), so it is no better than Flash-Lite's
  15 RPM for concurrency.
- Emits reasoning prose before the JSON, which defeated the old greedy regex.

It remains wired up as an opt-in overflow tier (`gemini_allow_gemma`), off by
default.

### Concurrency (4 simultaneous requests)

| | 1 request | 4 at once (wall) | Slowest |
|---|---|---|---|
| Ollama `qwen3-vl:4b` | 64.8s | **118.7s** | 118.7s |
| `gemini-3.5-flash-lite` | 1.68s | **1.25s** | 1.24s |

Ollama gives ~2.2 effective lanes, but the slowest of 4 took 118.7s — past the
integration's own 60s timeout. Local fails at multi-TV by blowing timeouts, not
by locking. Flash-Lite absorbs 4-at-once with no wall-clock penalty.

Note: VLM calls are per **tracked media player** (`_active_watchdogs[entity_id]`),
not per dashboard. Wall panels displaying Now Playing generate zero extra calls.

### Free-tier quota (per project; buckets are per-model and additive)

| Tier | Models | RPM | RPD | Role |
|---|---|---|---|---|
| Flash-Lite | 3.5, 3.1 (+2.5 burst) | 40 | **1020** | primary workhorse |
| Flash | 2.5, 3, 3.5, 3.6, 3.7 | 25 | **100** | escalation only |
| Gemma | 26B, 31B | 60 | 28800 | opt-in overflow |

Measured usage: ~1592 input tokens per call on Flash-Lite (1032 image + ~560
prompt). TPM is nowhere near binding — **requests are the scarce resource, not
tokens.** RPD resets at midnight Pacific.

Budget per TV: normal viewing ~30 req/hr, sports ~180 req/hr. One 3-hour game
is ~540 requests, about half the daily primary budget.

---

## 2. What was implemented

### New: `vlm_client.py`

- `VLMClient` base, `OllamaClient`, `GeminiClient`, `TieredVLM` facade.
- Per-model `ModelBudget`: sliding-window RPM + daily RPD, persisted via HA
  `Store` (key `ai_media_tracker.vlm_quota`) so a restart cannot overrun quota.
- Tier rotation: picks the first model in a tier with budget; on HTTP 429 it
  parks that model for 60s and rotates.
- Escalation: a weak primary result (`title` in `Browsing.../Unknown/null`)
  retries once on the Flash tier. Sports polling never escalates — too frequent
  to spend the 100 RPD budget on.
- Safety settings set to `BLOCK_NONE`; a blocked response yields no candidate
  and is treated as an empty read, which the consensus vote skips.
- `extract_json()` — balanced-brace scanner replacing the old greedy
  `re.search(r"\{.*\}")`. The old regex broke whenever a model emitted prose
  containing braces before the JSON.

### Changed

- `__init__.py` — both call sites route through `hass.data[...]["vlm_client"]`.
  `_check_for_progress_bar` timeout raised 10s → 30s. `async_preload_ollama_model`
  deleted in favour of `TieredVLM.preload()`.
- `config_flow.py` — provider dropdown, `gemini_api_key`, `gemini_escalate`,
  `gemini_allow_gemma`.
- `strings.json`, `translations/en.json` — labels and descriptions.
- `manifest.json` — `iot_class` → `cloud_polling`, version → 3.1.0.

### Verified

`dev/test_vlm_client.py` — 28 logic checks (extractor, budget accounting, tier
rotation, escalation predicate). Runs in plain Python, stubs the HA imports.

`dev/test_live.py` — end-to-end against the real API: sports frame 1.25s,
VOD+escalation 1.08s (NCIS with correct actors), trap frame passed, progress-bar
prompt 1.98s, **6 concurrent requests in 1.67s wall, 6/6 succeeded**.

### Privacy note

The Gemini path sends screenshots of whatever is on the TV to Google. Paid-tier
data is not used for training; **free-tier data is**. Flagged in the config-flow
description.

---

## 3. Open work

### Agreed, not yet implemented

1. **Capture the OSD frame during the pause.** `kickstart_metadata` pauses to
   surface the on-screen title/episode/platform overlay — a deliberate and good
   technique, since a random frame may show a title card or nothing useful. But
   `async_watchdog_loop` skips capture entirely while paused
   (`if state.state == "paused": sleep(2); continue`), and `kickstart_metadata`
   resumes playback immediately after creating the watchdog task. The task does
   not run until the caller yields, which happens on the `media_play` call — so
   the OSD window is always missed. `async_camera_image` makes it worse: ffmpeg
   with `-skip_frame nokey` waits for a keyframe under a 6s timeout, against an
   overlay that fades in 3–5s.

   Fix: capture inside `kickstart_metadata` while still paused, then resume,
   then analyse.

   ```python
   await ...media_pause..., blocking=True)
   await asyncio.sleep(1.5)                    # let the OSD render
   img = await cam.async_camera_image()        # capture WHILE paused
   await ...media_play..., blocking=True)      # resume immediately
   result = await _single_frame_analysis(..., img)
   ```

2. **Fold `is_browsing` into the main prompt; retire `_check_for_progress_bar`.**
   The progress-bar check exists to distinguish "browsing an app menu" from
   "paused inside a show" — both look like static UI to a VLM. It was a cheap
   early-out ahead of a 27s analysis; at 1.1s it is just a second request, and
   requests are the scarce resource. The composite already carries a high-res
   bottom strip and the VOD prompt already reasons about scrubbers (rules 3–4).
   Add explicit `is_browsing` / `has_scrubber` fields and read them directly.
   Halves requests on the pause path and lets the model use the whole frame
   instead of a 33% crop.

3. **Let the watchdog accept a pre-captured frame** so kickstart can hand one off.

### Note on a bug the migration already fixed

`_check_for_progress_bar` had a 10s timeout against 22–52s local inference, so
it returned `False` every time. The `else` branch forces `title = "Browsing..."`,
which means **pausing a show has always displayed "Browsing..."** — exactly the
confusion the check exists to prevent. The 30s timeout plus ~1.1s inference
resolves it.

---

## 4. Audit findings (not yet actioned)

### Correctness

1. **`pipeline/merge_state.py:92`** — `bool(len(native_title) > 4)`. `"NCIS"` is
   exactly 4 chars and is flagged as *not* native metadata. Also breaks `Lost`,
   `Cars`, `Dune`, `24`, `Up`. Should be `bool(native_title.strip())`.
2. **`__init__.py:171-172`** — `if len(check_title) > 15: return True` followed by
   `return True`. Both paths identical; the length test is dead.
3. **`__init__.py:225` and `:237`** — `guide_mgr.get_candidate_channels()` called
   twice with the same argument, with the same log line copy-pasted. Doubles
   Gracenote quota use on every native metadata update. Delete the first.
4. **`pipeline/merge_state.py:27`** — `sports_info` written unconditionally, so
   every movie carries a dict of `None`s. Gate on `if vlm_sport or vlm_teams:`.

### Fragility

5. **`__init__.py:344-369`** — reaches into `config_entry.runtime_data` and
   `hass.data["entity_components"]` for the pyatv object. Two fallback paths
   suggest it already broke once on an HA upgrade.
6. **`managers/routing_manager.py:32, 112, 298`** — identical 12-line block
   reading private `cam._attr_unique_id` / `cam._stream_source`. Extract a
   `_find_camera_by_slug()` helper; give `LinkedZoneCamera` a public property.
7. Five `except Exception: pass` swallow errors silently.

### Dead code and bloat

8. **`pipeline/merge_pipeline.py` is dead and has diverged.** `pipeline/resolver.py`
   imports `merge_native_and_vlm` from `merge_state`, not `merge_pipeline`. Two
   implementations of the same function with different logic. Delete it.
9. **Top-level `resolver.py`** (`MediaResolver`) — superseded by
   `pipeline/resolver.py`.
10. **`managers/guide_and_schedule_manager.py`** — unreferenced.
11. **5.4 MB of debug dumps**: `tournament.json` (4.8 MB),
    `tennis_scoreboard.json` (580 KB), plus zero-byte `breaking_bad.json` and
    `match_summary.json`.
12. **`fonts/` — 3.6 MB of Arial, and a licensing problem.** `arial.ttf` etc. are
    proprietary Monotype/Microsoft fonts; redistributing them is not permitted,
    which matters for HACS or GitHub. Swap for **Liberation Sans**
    (metric-compatible drop-in) or DejaVu Sans.

### Architecture

13. **`__init__.py` is ~1250 lines** of closures nested inside `async_setup_entry`,
    with `async def` bodies declared inside a `@callback`. The core state machine
    (state change → settle → kickstart → watchdog) cannot be unit-tested without
    booting HA. Extracting a `PlaybackCoordinator(hass, entry, player_name)` is
    the highest-value refactor available.
14. **No tests** beyond `dev/`. The stub-the-HA-imports trick used in
    `dev/test_vlm_client.py` works for `merge_state`, `vlm_stage`,
    `guardrail_manager` and `resolver` — all pure functions, and it would cover
    findings 1–4 above.
15. `_LOGGER.warning("MASTER DEBUG: ...")` fires on every state-change event;
    seven `"PyATV Debug:"` lines at INFO. 47 INFO calls total. Demote to debug.
16. `hass.data[DOMAIN][entry_id]` is an 18-key untyped dict addressed by string
    literals across every module.
17. 44 function-level imports, several shadowing module-level ones.

### Feature ideas

- **Multi-frame batching** — send 3 frames in one call and let the model
  reconcile, replacing the rolling-consensus vote. ~3× RPD reduction, and the
  quota shape (250K TPM vs 15 RPM) actively rewards fewer, larger requests.
- **Trim the consensus vote and 3-poll fast phase** — both exist to smooth over a
  flaky model; at 100% accuracy they are mostly wasted requests.
- **Quota diagnostic sensor** — `GeminiClient.quota_report()` already exists.
- **HA repair issues** (`async_create_issue`) on invalid key or exhausted quota.
- **Validate the API key in the config flow** with a live call before saving.
- **Composite at 768px wide** instead of 1024 — halves image tokens (4 tiles → 2).

---

## 5. Running the harness

Everything lives in `dev/`. Requires `Pillow` and `aiohttp`; no Home Assistant.

```bash
export GEMINI_API_KEY=...          # or put it in dev/gemini_key.txt (gitignored)

# accuracy + latency across all 13 frames
python dev/bench.py --gemini gemini-3.5-flash-lite --out results/fl.json
python dev/bench.py --ollama qwen3-vl:4b --out results/baseline_local.json

# N simultaneous requests
python dev/conc.py --gemini gemini-3.5-flash-lite --n 4
python dev/conc.py --ollama qwen3-vl:4b --n 4

# logic tests (no network, no API key needed)
python dev/test_vlm_client.py

# end-to-end against the real API
python dev/test_live.py
```

`bench.py` holds the ground truth for all 13 frames in `CASES`, including
`sports_test_9.jpg` — an off-angle phone photo whose ticker advertises a
*different* game (Georgia vs Tennessee) than the one on screen (Miss St vs
Georgia). It is the single most useful frame in the set for catching models that
read the wrong overlay.

**Never put the API key under `\config\www\`** — that directory is HTTP-served
by Home Assistant.
