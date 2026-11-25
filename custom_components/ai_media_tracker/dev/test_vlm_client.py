"""Test vlm_client logic without a running Home Assistant.

Stubs the homeassistant.* imports, then exercises the JSON extractor, the
per-model budget accounting, tier rotation, and the escalation predicate.
"""
import os
import sys
import types

# --- stub the HA modules vlm_client imports -------------------------------
for name in ("homeassistant", "homeassistant.helpers",
             "homeassistant.helpers.aiohttp_client", "homeassistant.helpers.storage"):
    sys.modules.setdefault(name, types.ModuleType(name))
sys.modules["homeassistant.helpers.aiohttp_client"].async_get_clientsession = lambda hass: None
sys.modules["homeassistant.helpers.storage"].Store = object

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import vlm_client as V

failures = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        print(f"        got {got!r}\n        want {want!r}")
        failures.append(label)


print("extract_json")
check("plain object",
      V.extract_json('{"a": 1}'), {"a": 1})
check("fenced json",
      V.extract_json('```json\n{"a": 1}\n```'), {"a": 1})
# The case that broke the old greedy regex: prose containing braces, then JSON.
prose = (
    'The user wants JSON.\n'
    '1. Schema is { "title": ... } roughly.\n'
    'Here is the answer:\n'
    '{"title": "NCIS", "show_info": {"actors": ["Wilmer Valderrama"]}}'
)
check("prose with braces then json",
      V.extract_json(prose),
      {"title": "NCIS", "show_info": {"actors": ["Wilmer Valderrama"]}})
check("braces inside strings",
      V.extract_json('{"desc": "a } brace { in text", "n": 2}'),
      {"desc": "a } brace { in text", "n": 2})
check("escaped quote in string",
      V.extract_json(r'{"desc": "say \"hi\" }", "n": 3}'),
      {"desc": 'say "hi" }', "n": 3})
check("no json", V.extract_json("nothing here"), None)
check("empty", V.extract_json(""), None)
check("malformed only", V.extract_json('{"a": '), None)

print("\nis_low_confidence")
check("browsing", V.is_low_confidence({"title": "Browsing..."}), True)
check("unknown", V.is_low_confidence({"title": "Unknown"}), True)
check("empty dict", V.is_low_confidence({}), True)
check("none", V.is_low_confidence(None), True)
check("real title", V.is_low_confidence({"title": "NCIS"}), False)
check("sports result", V.is_low_confidence({"sports_info": {"sport": "basketball"}}), False)

print("\nModelBudget")
b = V.ModelBudget("m", rpm=3, rpd=5)
check("starts available", b.available(), True)
for _ in range(3):
    b.consume()
check("rpm exhausted after 3", b.available(), False)
b._recent = []  # simulate the 60s window sliding
check("recovers after window", b.available(), True)
b.consume()
b.consume()
check("rpd exhausted at 5", b.available(), False)
check("used_today counted", b.used_today, 5)

b2 = V.ModelBudget("m2", rpm=10, rpd=10)
b2.penalise(60.0)
check("penalised is unavailable", b2.available(), False)

b3 = V.ModelBudget("m3", rpm=10, rpd=10, used_today=9, day="1999-01-01")
check("stale day resets counter", b3.available(), True)
check("counter zeroed on roll", b3.used_today, 0)

print("\ntier rotation")
c = V.GeminiClient.__new__(V.GeminiClient)
c._budgets = {}
for tier, models in V.GEMINI_TIERS.items():
    for model, rpm, rpd in models:
        c._budgets[model] = V.ModelBudget(model, rpm, rpd)

check("primary picks 3.5-flash-lite first",
      c._pick_model(V.TIER_PRIMARY), "gemini-3.5-flash-lite")
# exhaust the first model's RPD
c._budgets["gemini-3.5-flash-lite"].used_today = 500
check("rotates to 3.1 when 3.5 spent",
      c._pick_model(V.TIER_PRIMARY), "gemini-3.1-flash-lite")
c._budgets["gemini-3.1-flash-lite"].used_today = 500
check("rotates to 2.5 burst",
      c._pick_model(V.TIER_PRIMARY), "gemini-2.5-flash-lite")
c._budgets["gemini-2.5-flash-lite"].used_today = 20
check("primary exhausted -> None",
      c._pick_model(V.TIER_PRIMARY), None)
check("escalate tier still available",
      c._pick_model(V.TIER_ESCALATE), "gemini-3.5-flash")
check("overflow tier still available",
      c._pick_model(V.TIER_OVERFLOW), "gemma-4-26b-a4b-it")

print("\nquota totals declared")
prim = sum(rpd for _, _, rpd in V.GEMINI_TIERS[V.TIER_PRIMARY])
esc = sum(rpd for _, _, rpd in V.GEMINI_TIERS[V.TIER_ESCALATE])
check("primary RPD total = 1020", prim, 1020)
check("escalate RPD total = 100", esc, 100)

print()
if failures:
    print(f"{len(failures)} FAILURE(S): {failures}")
    sys.exit(1)
print("all checks passed")
