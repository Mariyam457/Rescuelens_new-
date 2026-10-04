"""
RescueLens - feature checker
============================

Run from the project folder (the one containing app.py):

    python check_features.py

It checks, with clear PASS / FAIL lines:
  1. Your .env Gemini settings
  2. A direct Gemini call (shows the REAL error if it fails)
  3. The AI Emergency Response Agent on 3 very different incidents
  4. The Nearby Emergency Services finder (OpenStreetMap)
  5. Manual-location geocoding

It never prints your API key and does not touch your database.
"""

import os
import sys

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ->  {detail}" if detail else ""))


def section(title):
    print(f"\n=== {title} ===")


def redact(text):
    key = os.getenv("GEMINI_API_KEY", "")
    return text.replace(key, "<key>") if key else text


# ---------------------------------------------------------------
section("1. Gemini settings (.env)")
# ---------------------------------------------------------------
key = os.getenv("GEMINI_API_KEY", "").strip()
model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()

check("GEMINI_API_KEY is set", bool(key))
is_placeholder = key.lower().startswith("your") or "your_" in key.lower()
check(
    "GEMINI_API_KEY is a real key, not the placeholder",
    bool(key) and not is_placeholder,
    "looks like the placeholder - replace it in .env" if is_placeholder else f"length {len(key)}",
)
check("GEMINI_MODEL is set", bool(model), model)

try:
    from google import genai
    from google.genai import types

    check("google-genai is installed", True)
except Exception as exc:
    genai = None
    check("google-genai is installed", False, "run: pip install -r requirements.txt")


# ---------------------------------------------------------------
section("2. Direct Gemini call")
# ---------------------------------------------------------------
if genai and key and not is_placeholder:
    try:
        client = genai.Client(api_key=key)
        reply = client.models.generate_content(
            model=model, contents="Reply with the single word: OK"
        )
        check("Gemini responds", True, (reply.text or "").strip()[:40])
    except Exception as exc:
        check("Gemini responds", False, redact(f"{type(exc).__name__}: {str(exc)[:300]}"))
else:
    check("Gemini responds", False, "skipped - fix section 1 first")


# ---------------------------------------------------------------
section("3. AI Emergency Response Agent")
# ---------------------------------------------------------------
from emergency_agent import analyze_emergency

SCENARIOS = [
    {
        "label": "Building fire, people trapped",
        "emergency": {
            "id": "t1",
            "description": "Fire in a 3-storey building, thick smoke, 4 children trapped on the top floor.",
            "location": "Gulberg, Lahore",
            "latitude": 31.5204,
            "longitude": 74.3587,
            "people_affected": 4,
            "emergency_type": "Fire",
            "priority": "Critical",
            "people_trapped": 1,
            "medical_emergency": 0,
            "critical_injury": 0,
            "required_team": "Fire Team",
            "status": "Waiting for responder",
            "ai_summary": "",
        },
        "expect_evacuation": True,
        "allowed_severity": ["Critical"],
        "keywords": ["fire", "evacuat"],
    },
    {
        "label": "Flooded street",
        "emergency": {
            "id": "t2",
            "description": "Flood water is entering homes in our street, water is waist deep, elderly people are stuck.",
            "location": "Model Town, Lahore",
            "latitude": 31.4846,
            "longitude": 74.3248,
            "people_affected": 12,
            "emergency_type": "Flood",
            "priority": "High",
            "people_trapped": 1,
            "medical_emergency": 0,
            "critical_injury": 0,
            "required_team": "Rescue Team",
            "status": "Waiting for responder",
            "ai_summary": "",
        },
        "expect_evacuation": True,
        "allowed_severity": ["Critical", "High"],
        "keywords": ["water", "rescue"],
    },
    {
        "label": "Minor supply request",
        "emergency": {
            "id": "t3",
            "description": "Our family has no drinking water since morning, everyone is safe and calm.",
            "location": "Johar Town, Lahore",
            "latitude": 31.4697,
            "longitude": 74.2728,
            "people_affected": 5,
            "emergency_type": "Food/Water",
            "priority": "Low",
            "people_trapped": 0,
            "medical_emergency": 0,
            "critical_injury": 0,
            "required_team": "Food/Water Team",
            "status": "Waiting for responder",
            "ai_summary": "",
        },
        "expect_evacuation": False,
        "allowed_severity": ["Low", "Medium"],
        "keywords": ["water"],
    },
]

outputs = []
for sc in SCENARIOS:
    print(f"\n--- {sc['label']} ---")
    res = analyze_emergency(sc["emergency"])
    a = res["assessment"]

    check("Gemini produced the answer (not basic guidance)", res["ai_generated"], redact(res["error"] or ""))

    if res["ai_generated"]:
        print(f"    Incident: {a['incident_type']} | Severity: {a['severity']} | Priority: {a['priority_level']} | Evacuation: {a['evacuation_required']}")
        print(f"    Resources: {', '.join(a['required_resources'])}")
        for i, step in enumerate(a["immediate_actions"][:3], 1):
            print(f"    Action {i}: {step}")

        check("Severity is sensible for this incident", a["severity"] in sc["allowed_severity"], f"got {a['severity']}, expected one of {sc['allowed_severity']}")
        check("Evacuation decision is sensible", a["evacuation_required"] == sc["expect_evacuation"], f"got {a['evacuation_required']}")
        check("Has actions, resources, plan and precautions", all([a["immediate_actions"], a["required_resources"], a["response_plan"], a["safety_precautions"]]))
        blob = " ".join(a["immediate_actions"] + a["response_plan"] + a["required_resources"]).lower()
        check("Mentions the relevant hazard", any(k in blob for k in sc["keywords"]), f"looked for {sc['keywords']}")
        outputs.append(blob)

if len(outputs) == len(SCENARIOS):
    check("Answers differ between incidents (dynamic, not hardcoded)", len(set(outputs)) == len(outputs))


# ---------------------------------------------------------------
section("4. Nearby Emergency Services (OpenStreetMap)")
# ---------------------------------------------------------------
from nearby_services import find_nearby_services, geocode_location

RADIUS = 5
res = find_nearby_services(31.5204, 74.3587, RADIUS)
check("Search completed", res["ok"], res["error"] or "")

if res["ok"]:
    services = res["services"]
    check("Found at least one service", len(services) > 0, f"{len(services)} found")

    by_cat = {}
    for s in services:
        by_cat.setdefault(s["category"], []).append(s)
    for cat, items in by_cat.items():
        nearest = items[0]
        print(f"    {nearest['category_label']}: {len(items)} found, nearest = {nearest['name']} ({nearest['distance_km']:.1f} km)")

    check("All results are inside the radius", all(s["distance_km"] <= RADIUS for s in services))
    check("Results are sorted by distance", [s["distance_km"] for s in services] == sorted(s["distance_km"] for s in services))
    check("Every result has a directions link", all(s["directions_url"].startswith("https://") for s in services))
    check("Hospitals and police stations are found (Lahore city centre)", "hospital" in by_cat and "police" in by_cat)


# ---------------------------------------------------------------
section("5. Manual location geocoding")
# ---------------------------------------------------------------
geo = geocode_location("Minar-e-Pakistan, Lahore")
check("Place name converted to coordinates", geo is not None, f"{geo[0]:.4f}, {geo[1]:.4f}" if geo else "no result (internet?)")
if geo:
    check("Coordinates are in the Lahore area", 31.3 < geo[0] < 31.7 and 74.1 < geo[1] < 74.6)


# ---------------------------------------------------------------
print(f"\n=== SUMMARY: {sum(results)} passed, {len(results) - sum(results)} failed ===")
sys.exit(0 if all(results) else 1)
