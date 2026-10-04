"""
Regression tests for the RescueLens fixes.  Run:   python test_fixes.py

Uses only the offline (keyword) triage, so no API key or internet is needed.
Real emergencies are included on purpose: the keyword fixes must never make a
genuine emergency look less serious.
"""

import os
import sys

os.environ["GEMINI_API_KEY"] = ""          # force the offline fallback
os.environ["SUPABASE_URL"] = ""            # never touch the real cloud database
os.environ["SUPABASE_KEY"] = ""
os.environ["LOCAL_DB_PATH"] = os.path.join(
    __import__("tempfile").gettempdir(), "rescuelens_test.db"
)

from ai_service import triage_emergency   # noqa: E402

RANK = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
failures = []


def check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ->  {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def t(text, people=None):
    return triage_emergency(text, people)


# ---------------------------------------------------------------
print("\n=== False positives that the audit found (must NOT be emergencies) ===")
# ---------------------------------------------------------------
r = t("Diesel delivery truck is parked outside the shop, please send the receipt")
check("'diesel' is not a medical emergency", not r["medical_emergency"] and r["emergency_type"] != "Medical", f"{r['emergency_type']} / {r['priority']}")

r = t("There will be fireworks at the wedding tonight near our street")
check("'fireworks' is not a Fire emergency", r["emergency_type"] != "Fire" and r["priority"] != "Critical", f"{r['emergency_type']} / {r['priority']}")

r = t("Our school will hold a fire drill tomorrow morning at ten")
check("'fire drill' is not a Fire emergency", r["emergency_type"] != "Fire" and r["priority"] != "Critical", f"{r['emergency_type']} / {r['priority']}")

r = t("I am painting the house and need a ladder, spain trip next week")
check("'painting'/'spain' do not trigger 'pain'", not r["medical_emergency"], f"{r['emergency_type']}")

# ---------------------------------------------------------------
print("\n=== Corrected classifications ===")
# ---------------------------------------------------------------
r = t("Water has entered our home and the street is full of water")
check("'water has entered our home' is a Flood", r["emergency_type"] == "Flood", f"{r['emergency_type']} / {r['priority']}")

r = t("Flood water has entered our house. Six people are trapped inside, and one person is unconscious.")
check("'Six people' is counted as 6", r["people_affected"] == 6, f"{r['people_affected']}")
check("Flood + trapped + unconscious is Critical", r["priority"] == "Critical", r["priority"])

r = t("Four children are stuck on the roof", 1)
check("Number word beats the form's default of 1", r["people_affected"] == 4, f"{r['people_affected']}")

r = t("Flood water, 3 people", 10)
check("A larger value typed in the form is kept", r["people_affected"] == 10, f"{r['people_affected']}")

# ---------------------------------------------------------------
print("\n=== Urdu script ===")
# ---------------------------------------------------------------
r = t("ہمارے گھر میں آگ لگ گئی ہے اور بچے اندر پھنسے ہوئے ہیں")
check("Urdu script fire + trapped -> Fire / Critical", r["emergency_type"] == "Fire" and r["priority"] == "Critical", f"{r['emergency_type']} / {r['priority']}")

r = t("سیلاب کا پانی گھر میں آ گیا ہے")
check("Urdu script flood -> Flood", r["emergency_type"] == "Flood", f"{r['emergency_type']}")

r = t("وہ آگے جا رہا ہے اور دکان کھلی ہے")
check("Urdu 'aage' (ahead) is not fire", r["emergency_type"] != "Fire", f"{r['emergency_type']}")

# ---------------------------------------------------------------
print("\n=== Real emergencies must stay serious (no under-triage) ===")
# ---------------------------------------------------------------
real = [
    ("Building on fire, people trapped on the third floor", "Fire", "Critical"),
    ("Mere ghar mein aag lag gayi hai, 4 bachy ander hin", "Fire", "Critical"),
    ("A man is unconscious and not breathing", "Medical", "Critical"),
    ("Building collapsed, people under the rubble", None, "Critical"),
    ("The area flooded, water rising fast, families stranded", "Flood", "High"),
    ("Car accident, two people injured and bleeding", None, "High"),
    ("There was an explosion at the factory, workers injured", "Fire", "High"),
    ("Flooded street, we are stuck in the car", "Flood", "High"),
]
for text, want_type, want_min in real:
    r = t(text)
    ok = RANK[r["priority"]] <= RANK[want_min] and (want_type is None or r["emergency_type"] == want_type)
    check(text[:55], ok, f"{r['emergency_type']} / {r['priority']}")

# ---------------------------------------------------------------
print("\n=== Placeholder key does not call the API ===")
# ---------------------------------------------------------------
os.environ["GEMINI_API_KEY"] = "your_gemini_api_key"
r = t("Fire in the house, people trapped")
check("Placeholder key -> offline triage, ai_generated False", r["ai_generated"] is False and r["priority"] == "Critical")

# ---------------------------------------------------------------
print("\n=== Escaping (stored-XSS / PDF crash) ===")
# ---------------------------------------------------------------
from html import escape                                     # noqa: E402
hostile = '<img src=x onerror=alert(1)><b>'
check("html.escape neutralises markup", "<" not in escape(hostile))

try:
    import app   # noqa: F401  (imports reportlab etc.)
    from app import generate_rescue_report_pdf
    rows = [{
        "id": "x1", "display_number": 1001, "description": "d", "reporter_name": "<b",
        "reporter_phone": "1", "latitude": 1.0, "longitude": 1.0,
        "location": "<font size=", "emergency_type": "Fire", "priority": "High",
        "required_team": "Fire Team", "people_affected": 2, "medical_emergency": 0,
        "people_trapped": 0, "critical_injury": 0, "ai_summary": "<para>", "ai_reasoning": "",
        "ai_generated": 0, "status": "Waiting for responder",
        "created_at": "2026-10-01T10:00:00+00:00", "updated_at": "2026-10-01T10:00:00+00:00",
        "assignment": None,
    }]
    pdf = generate_rescue_report_pdf(rows)
    check("PDF builds with '<b' and '<font' in names", bool(pdf) and len(pdf) > 1000, f"{len(pdf)} bytes")
except Exception as exc:                                      # pragma: no cover
    check("PDF builds with '<b' and '<font' in names", False, f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------
print("\n=== Map popups escape hostile text ===")
# ---------------------------------------------------------------
try:
    import map_utils
    captured = {}
    map_utils.st_folium = lambda m, **kw: captured.update(map=m, kwargs=kw)

    evil = '<img src=x onerror=alert(1)>'
    report = {
        "id": "e1", "display_number": 1001, "latitude": 29.4, "longitude": 71.7,
        "priority": "High", "emergency_type": "Fire", "status": "Waiting for responder",
        "ai_summary": evil, "description": evil, "assignment": None,
    }
    responder = {"id": "r1", "name": evil, "type": "Fire Team", "latitude": 29.4, "longitude": 71.7, "available": 1}

    map_utils.render_map([report], [responder], None)
    html = captured["map"].get_root().render()
    check("No raw <img onerror> reaches the map HTML", "<img src=x onerror" not in html)
    check("Escaped text is present instead", "&lt;img src=x onerror" in html)
    check("Map does not rerun the app on every pan/zoom", captured["kwargs"].get("returned_objects") == [])
except Exception as exc:                                      # pragma: no cover
    check("Map popup escaping test ran", False, f"{type(exc).__name__}: {exc}")

# ---------------------------------------------------------------
print("\n=== Gemini 'temperature' rejection is handled ===")
# ---------------------------------------------------------------
try:
    import json
    import types as _t
    import ai_service
    import google.genai as _g

    seen = []

    class _Models:
        def generate_content(self, model, contents, config):
            seen.append(getattr(config, "temperature", None))
            if getattr(config, "temperature", None) is not None:
                raise Exception("400 INVALID_ARGUMENT: temperature is not supported")
            return _t.SimpleNamespace(text=json.dumps({
                "emergency_type": "Fire", "priority": "Critical", "required_team": "Fire Team",
                "people_affected": 2, "medical_emergency": False, "people_trapped": True,
                "critical_injury": False, "summary": "Fire with people trapped.", "reasoning": "ok",
            }))

    ai_service.genai = _t.SimpleNamespace(Client=lambda **k: _t.SimpleNamespace(models=_Models()))
    os.environ["GEMINI_API_KEY"] = "AQ.real-looking-key"
    r = triage_emergency("Fire in the house and two people are trapped inside")
    check("Triage retries without temperature and uses Gemini", r["ai_generated"] is True and len(seen) == 2, f"calls={len(seen)}")
except Exception as exc:                                      # pragma: no cover
    check("Temperature-retry test ran", False, f"{type(exc).__name__}: {exc}")

print(f"\n=== SUMMARY: {len(failures)} failed ===")
sys.exit(1 if failures else 0)
