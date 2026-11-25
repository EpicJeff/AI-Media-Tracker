"""VLM benchmark for AI Media Tracker.

Compares Google Gemini API models (Gemma 26B, Flash-Lite, Flash) against the
current local Ollama baseline (qwen3-vl:4b) on real captured frames.

Uses the EXACT composite preprocessing (pipeline/vlm_stage.py) and the EXACT
prompts from __init__.py so results transfer directly to the integration.
"""
import argparse
import base64
import io
import json
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.request

from PIL import Image

TESTDIR = os.environ.get("AI_MEDIA_TEST_FRAMES", "test")
OLLAMA = os.environ.get("OLLAMA_URL", "http://localhost:11434")
GEMINI = "https://generativelanguage.googleapis.com/v1beta"


# ---------------------------------------------------------------- preprocessing
def optimize_image_for_vlm(raw_bytes):
    """EXACT copy of pipeline/vlm_stage.py:optimize_image_for_vlm."""
    img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
    w, h = img.size
    full_frame = img.resize((1024, int(1024 * (h / w))), Image.Resampling.LANCZOS)
    top_raw = img.crop((0, 0, w, int(h * 0.20)))
    bot_raw = img.crop((0, int(h * 0.75), w, h))
    top = top_raw.resize((1024, int(1024 * (top_raw.height / top_raw.width))), Image.Resampling.LANCZOS)
    bot = bot_raw.resize((1024, int(1024 * (bot_raw.height / bot_raw.width))), Image.Resampling.LANCZOS)
    total_h = full_frame.height + top.height + bot.height
    comp = Image.new("RGB", (1024, total_h))
    comp.paste(full_frame, (0, 0))
    comp.paste(top, (0, full_frame.height))
    comp.paste(bot, (0, full_frame.height + top.height))
    out = io.BytesIO()
    comp.save(out, format="JPEG", quality=80, optimize=True)
    return out.getvalue()


def crop_progress_bar(raw_bytes):
    """EXACT crop from __init__.py:_check_for_progress_bar."""
    img = Image.open(io.BytesIO(raw_bytes))
    w, h = img.size
    cropped = img.crop((0, int(h * 0.66), w, h)).convert("RGB")
    buf = io.BytesIO()
    cropped.save(buf, format="JPEG")
    return buf.getvalue()


# ---------------------------------------------------------------- prompts (verbatim)
PROMPT_PROGRESS = (
    'Does this image contain a video progress bar or scrubber timeline? '
    'Reply strictly with a JSON object: {"has_progress_bar": true} or '
    '{"has_progress_bar": false}'
)

PROMPT_SPORTS = (
    "Analyze this video frame from a sports broadcast and return a strict JSON object.\n\n"
    "### CRITICAL INSTRUCTION:\n"
    "1. Identify the sport and league (e.g., 'tennis' and 'atp', or 'basketball' and 'nba').\n"
    "2. Identify the Tournament Name or Event Name.\n"
    "3. Identify the Venue or Stadium name if visible.\n"
    "4. For team sports, set 'show_info.episode' to 'Team A vs Team B'.\n"
    "5. For individual sports, set 'show_info.episode' to 'Player A vs Player B' or 'Tournament Name'.\n"
    "6. For TENNIS: Carefully read the score bug. Completed sets are in the left columns, the current "
    "set is next. The current game score (e.g. 15, 30, 40, AD, or tiebreak points like 4, 5, 6) is "
    "completely separate and usually on the far right or in a separate box. NEVER combine the game "
    "score or tiebreak points with the set scores. Look for a serving indicator (a dot or tennis ball "
    "icon) next to a player's name.\n"
    "7. Extract any visible game state metrics from the score bug. For tennis, extract ALL sets played "
    "so far in chronological order.\n\n"
    "Output JSON ONLY using this exact structure:\n"
    "{\n"
    '  "sports_info": {\n'
    '    "sport": "sport name or null",\n'
    '    "league": "league name or null",\n'
    '    "tournament": "tournament name or null",\n'
    '    "venue": "venue name or null",\n'
    '    "game_state": {\n'
    '      "clock": "10:24 or null",\n'
    '      "period": "3 or null",\n'
    '      "inning": "Top 4th or null",\n'
    '      "outs": "2 or null",\n'
    '      "count": "3-2 or null",\n'
    '      "hole": "14 or null",\n'
    '      "tennis_sets": [{"competitor_a": 6, "competitor_b": 4}],\n'
    '      "tennis_game": "30-40 or null",\n'
    '      "server": "name of serving player or null"\n'
    "    },\n"
    '    "competitors": [\n'
    '      {"name": "Competitor A"},\n'
    '      {"name": "Competitor B"}\n'
    "    ]\n"
    "  },\n"
    '  "show_info": {\n'
    '    "episode": ["Team A vs Team B"]\n'
    "  }\n"
    "}"
)

PROMPT_VOD = (
    "Analyze this video frame from a TV show or movie and return a strict JSON object.\n\n"
    "### CRITICAL INSTRUCTION:\n"
    "1. ESCAPE HATCH: If this is actually a live sports broadcast, simply set 'is_sports_broadcast': "
    "true and return.\n"
    "2. If the image shows the main Apple TV home screen (a grid of app icons like Netflix, Settings, "
    "Photos, App Store, etc., with no specific media playing or highlighted), simply return "
    "'Browsing...' as the title and return.\n"
    "3. IMPORTANT: Check for a full-width video scrubber/timeline (usually at the absolute bottom edge "
    "of the screen, indicating current timestamp and total duration). If this full-width scrubber is "
    "visible, it means a video is actively paused. In this case, DO NOT return 'Browsing...'-proceed "
    "to identify the show.\n"
    "4. If there is NO full-width video scrubber (ignore small progress bars underneath episode "
    "thumbnails or 'Resume' buttons), and the screen is a menu, grid, browsing interface, or an "
    "episode/movie details screen, simply return 'Browsing...' as the title and return.\n"
    "5. If it is not a menu or details screen, identify the name of the specific episode if it is shown "
    "on screen (e.g., during an intro sequence).\n"
    "6. Identify any visible actors by name.\n"
    "7. Identify the main title of the series or movie.\n\n"
    "Output JSON ONLY using this exact structure:\n"
    "{\n"
    '  "title": "Name of Movie or Show",\n'
    '  "is_sports_broadcast": false,\n'
    '  "show_info": {\n'
    '    "episode": ["Name of episode"],\n'
    '    "actors": ["Actor A", "Actor B"]\n'
    "  },\n"
    '  "scene_analysis": {\n'
    '    "mood_tags": ["Action", "Drama"],\n'
    '    "visual_description": "Briefly describe what is happening"\n'
    "  }\n"
    "}"
)


# ---------------------------------------------------------------- ground truth
# task: which prompt to use. expect: substrings that must appear (case-insensitive)
#   in the flattened JSON. avoid: substrings that must NOT appear (distractor traps).
CASES = {
    "sports_test.jpg": dict(
        task="sports", desc="NCAA basketball, Kansas 4 - Houston 2, 17:20 1st",
        expect=["basketball", "kansas", "houston"], avoid=[],
        detail={"clock": "17:20", "period": "1"}),
    "sports_test_2.jpg": dict(
        task="sports", desc="NBA ECF, MIA 0 - BOS 2, 11:06 1st",
        expect=["basketball", "nba", "miami|mia", "boston|bos"], avoid=[],
        detail={"clock": "11:06"}),
    "sports_test_3.jpg": dict(
        task="sports", desc="NFL, Texans 3 - Chiefs 13, 2nd QTR 4:36",
        expect=["football", "nfl", "texans|houston", "chiefs|kansas"], avoid=[],
        detail={"clock": "4:36", "period": "2"}),
    "sports_test_4.jpg": dict(
        task="sports", desc="MLB, White Sox 4 - Royals 4, MID 6TH",
        expect=["baseball", "sox", "royals"], avoid=[],
        detail={"inning": "6"}),
    "sports_test_5.jpg": dict(
        task="sports", desc="Premier League, CHE 0 - LIV 0, 17:26",
        expect=["soccer|football", "premier", "chelsea|che", "liverpool|liv"], avoid=[],
        detail={"clock": "17:26"}),
    "sports_test_6.jpg": dict(
        task="sports", desc="Brazilian soccer, CAM 0 - BRA 0, 1o tempo 05:27",
        expect=["soccer|football", "cam|atl", "bra"], avoid=[],
        detail={"clock": "05:27"}),
    "sports_test_7.jpg": dict(
        task="sports", desc="Beijing 2022 Olympics figure skating, NO scorebug",
        expect=["skating|olympic"], avoid=[],
        detail={}),
    "sports_test_8.jpg": dict(
        task="sports", desc="ICC World T20 2016 cricket, BAN v IND 35-0 (5.3)",
        expect=["cricket", "ban|bangladesh", "ind|india"], avoid=[],
        detail={}),
    "sports_test_9.jpg": dict(
        task="sports", desc="TRAP: off-angle photo. SEC softball Miss St 0 - Georgia 0. "
                            "Ticker advertises a DIFFERENT game (Georgia vs Tennessee).",
        expect=["softball|baseball", "mississippi|miss st", "georgia"],
        avoid=["tennessee"], detail={}),
    "movie_test.jpg": dict(
        task="vod", desc="KPop Demon Hunters (Netflix)",
        expect=["kpop|k-pop", "demon hunters"], avoid=[], detail={}),
    "tvshow_test.jpg": dict(
        task="vod", desc="NCIS (CBS bug, bottom right)",
        expect=["ncis"], avoid=[], detail={}),
    "tvshow_test_2.jpg": dict(
        task="vod", desc="NCIS title card",
        expect=["ncis"], avoid=[], detail={}),
    "news_test.jpg": dict(
        task="vod", desc="CNBC Halftime Report - Debate: Best Q4 Strategies",
        expect=["cnbc|halftime"], avoid=[], detail={}),
}


# ---------------------------------------------------------------- transport
def extract_json(reply):
    """Robust extractor: scan for balanced top-level {...} blocks, return the
    largest one that parses. Handles models that emit reasoning prose before the
    JSON (Gemma does this), which defeats a greedy \\{.*\\} regex."""
    if not reply:
        return None
    if "`" in reply:
        reply = re.sub(r"`{3}(?:json)?", "", reply)
    cands = []
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(reply):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    cands.append(reply[start:i + 1])
    best = None
    for blob in sorted(cands, key=len, reverse=True):
        try:
            obj = json.loads(blob)
        except Exception:
            continue
        if isinstance(obj, dict) and obj:
            best = obj
            break
    return best


def _post(url, payload, timeout, headers=None):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def call_ollama(model, prompt, img_bytes, timeout=600):
    b64 = base64.b64encode(img_bytes).decode()
    t0 = time.time()
    try:
        res = _post(f"{OLLAMA}/api/generate",
                    {"model": model, "prompt": prompt, "images": [b64],
                     "stream": False, "format": "json"}, timeout)
        dt = time.time() - t0
        reply = (res.get("response") or "").strip() or (res.get("thinking") or "").strip()
        return {"ok": True, "sec": dt, "json": extract_json(reply), "raw": reply[:500]}
    except Exception as e:
        return {"ok": False, "sec": time.time() - t0, "json": None,
                "raw": f"ERR {type(e).__name__}: {e}"[:300]}


SAFETY = [{"category": c, "threshold": "BLOCK_NONE"} for c in (
    "HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
    "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT")]


def call_gemini(model, prompt, img_bytes, key, timeout=180):
    b64 = base64.b64encode(img_bytes).decode()
    body = {
        "contents": [{"parts": [
            {"text": prompt},
            {"inline_data": {"mime_type": "image/jpeg", "data": b64}},
        ]}],
        "generationConfig": {"temperature": 0},
    }
    # Gemma on the Gemini API does not accept responseMimeType or safetySettings.
    if model.startswith("gemma"):
        pass
    else:
        body["generationConfig"]["responseMimeType"] = "application/json"
        body["safetySettings"] = SAFETY
    t0 = time.time()
    try:
        res = _post(f"{GEMINI}/models/{model}:generateContent?key={key}", body, timeout)
        dt = time.time() - t0
        cands = res.get("candidates") or []
        if not cands:
            fb = json.dumps(res.get("promptFeedback", {}))[:200]
            return {"ok": False, "sec": dt, "json": None, "raw": f"NO_CANDIDATE {fb}"}
        parts = cands[0].get("content", {}).get("parts") or []
        reply = "".join(p.get("text", "") for p in parts).strip()
        u = res.get("usageMetadata", {})
        return {"ok": True, "sec": dt, "json": extract_json(reply), "raw": reply[:500],
                "finish": cands[0].get("finishReason"),
                "in_tok": u.get("promptTokenCount"), "out_tok": u.get("candidatesTokenCount")}
    except urllib.error.HTTPError as e:
        return {"ok": False, "sec": time.time() - t0, "json": None,
                "raw": f"HTTP {e.code}: {e.read().decode()[:250]}"}
    except Exception as e:
        return {"ok": False, "sec": time.time() - t0, "json": None,
                "raw": f"ERR {type(e).__name__}: {e}"[:250]}


# ---------------------------------------------------------------- scoring
def flatten(obj):
    if obj is None:
        return ""
    return json.dumps(obj, ensure_ascii=False).lower()


def score(result, case):
    """Return (hits, total, trap_triggered)."""
    blob = flatten(result.get("json"))
    if not blob or blob == "null":
        return 0, len(case["expect"]), False
    hits = 0
    for term in case["expect"]:
        if any(alt.strip() in blob for alt in term.split("|")):
            hits += 1
    trap = any(t in blob for t in case["avoid"])
    return hits, len(case["expect"]), trap


# ---------------------------------------------------------------- runner
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default=os.environ.get("GEMINI_API_KEY", ""))
    ap.add_argument("--gemini", nargs="*", default=[], help="Gemini/Gemma model ids")
    ap.add_argument("--ollama", nargs="*", default=[], help="Ollama model tags")
    ap.add_argument("--only", nargs="*", default=[], help="subset of frame filenames")
    ap.add_argument("--repeat", type=int, default=1, help="runs per frame (latency stability)")
    ap.add_argument("--out", default="results.json")
    args = ap.parse_args()

    frames = {}
    for name in sorted(CASES):
        if args.only and name not in args.only:
            continue
        path = os.path.join(TESTDIR, name)
        raw = open(path, "rb").read()
        frames[name] = {"composite": optimize_image_for_vlm(raw), "progress": crop_progress_bar(raw)}

    comp_kb = statistics.mean(len(f["composite"]) for f in frames.values()) / 1024
    print(f"{len(frames)} frames | mean composite {comp_kb:.0f}KB\n")

    targets = [("gemini", m) for m in args.gemini] + [("ollama", m) for m in args.ollama]
    results = {}

    for kind, model in targets:
        print(f"=== {model} ({kind}) ===")
        rows = []
        for name in sorted(frames):
            case = CASES[name]
            prompt = PROMPT_SPORTS if case["task"] == "sports" else PROMPT_VOD
            img = frames[name]["composite"]
            best = None
            times = []
            for _ in range(args.repeat):
                r = (call_gemini(model, prompt, img, args.key) if kind == "gemini"
                     else call_ollama(model, prompt, img))
                times.append(r["sec"])
                if best is None or (r.get("json") and not best.get("json")):
                    best = r
            hits, total, trap = score(best, case)
            med = statistics.median(times)
            flag = "TRAP" if trap else ("OK" if hits == total else "")
            status = "" if best["ok"] else "FAIL"
            print(f"  {name:20s} {med:6.2f}s  {hits}/{total} {flag:5s} {status}")
            if not best["ok"] or hits < total:
                print(f"      -> {best['raw'][:220]}")
            rows.append({"frame": name, "sec": med, "hits": hits, "total": total,
                         "trap": trap, "ok": best["ok"], "json": best.get("json"),
                         "raw": best["raw"], "in_tok": best.get("in_tok"),
                         "out_tok": best.get("out_tok")})
        ok_rows = [r for r in rows if r["ok"]]
        tot_h = sum(r["hits"] for r in rows)
        tot_t = sum(r["total"] for r in rows)
        traps = sum(1 for r in rows if r["trap"])
        med_all = statistics.median([r["sec"] for r in ok_rows]) if ok_rows else float("nan")
        toks = [r["in_tok"] for r in rows if r.get("in_tok")]
        tok_s = f" | mean {statistics.mean(toks):.0f} in-tok" if toks else ""
        print(f"  SUMMARY {tot_h}/{tot_t} fields ({100*tot_h/tot_t:.0f}%) | "
              f"median {med_all:.2f}s | {traps} trap | {len(rows)-len(ok_rows)} fail{tok_s}\n")
        results[model] = {"kind": kind, "rows": rows, "median_sec": med_all,
                          "hits": tot_h, "total": tot_t, "traps": traps}

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=1)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
