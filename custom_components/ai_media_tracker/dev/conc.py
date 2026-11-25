"""Concurrency test: simulate N TVs polling simultaneously.

Answers: does the local Ollama box serialize requests, and does the hosted API
actually absorb N-at-once? Measures wall-clock for N simultaneous requests vs
the single-request baseline.
"""
import argparse
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bench import (CASES, PROMPT_SPORTS, TESTDIR, call_gemini, call_ollama,
                   optimize_image_for_vlm)

FRAMES = ["sports_test.jpg", "sports_test_2.jpg", "sports_test_3.jpg",
          "sports_test_5.jpg", "sports_test_6.jpg", "sports_test_8.jpg"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default="")
    ap.add_argument("--gemini", default="")
    ap.add_argument("--ollama", default="")
    ap.add_argument("--n", type=int, default=4, help="simultaneous requests")
    args = ap.parse_args()

    imgs = [optimize_image_for_vlm(open(os.path.join(TESTDIR, f), "rb").read())
            for f in FRAMES[:args.n]]

    if args.gemini:
        kind, model = "gemini", args.gemini
        fn = lambda img: call_gemini(model, PROMPT_SPORTS, img, args.key)
    else:
        kind, model = "ollama", args.ollama
        fn = lambda img: call_ollama(model, PROMPT_SPORTS, img)

    print(f"{model} ({kind}) | N={args.n} simultaneous\n")

    # 1 single request = baseline
    t0 = time.time()
    base = fn(imgs[0])
    t_single = time.time() - t0
    print(f"  baseline (1 request)      : {t_single:6.2f}s  ok={base['ok']} json={base['json'] is not None}")

    # N at once
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.n) as ex:
        results = list(ex.map(fn, imgs))
    wall = time.time() - t0
    lats = [r["sec"] for r in results]
    okc = sum(1 for r in results if r["ok"])
    jsonc = sum(1 for r in results if r.get("json"))

    print(f"  {args.n} simultaneous          : {wall:6.2f}s wall")
    print(f"     per-request: min {min(lats):.2f}s  median {statistics.median(lats):.2f}s  max {max(lats):.2f}s")
    print(f"     ok {okc}/{args.n}  parsed-json {jsonc}/{args.n}")

    serial_est = t_single * args.n
    print(f"\n  if fully serialized: ~{serial_est:.1f}s | if fully parallel: ~{t_single:.1f}s")
    ratio = wall / t_single if t_single else 0
    if ratio < 1.5:
        verdict = f"FULLY PARALLEL (wall {ratio:.1f}x single)"
    elif ratio > args.n * 0.75:
        verdict = f"SERIALIZED (wall {ratio:.1f}x single)"
    else:
        verdict = f"PARTIAL ({ratio:.1f}x single, ~{args.n/ratio:.1f} effective lanes)"
    print(f"  VERDICT: {verdict}")

    for r in results:
        if not r["ok"]:
            print(f"     FAIL: {r['raw'][:160]}")


if __name__ == "__main__":
    main()
