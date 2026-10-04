import os
os.environ.pop("GEMINI_API_KEY", None)
from ai_service import triage_emergency

cases = [
    ("Flood water has entered our house. Six people are trapped and one person is unconscious.", 6),
    ("Our road is flooded and we need drinking water.", 4),
    ("There is smoke and fire inside a shop.", 3),
]
for text, people in cases:
    r = triage_emergency(text, people)
    assert r["priority"] in {"Critical","High","Medium","Low"}
    assert r["emergency_type"] in {"Medical","Flood","Fire","Rescue","Food/Water","Other"}
    assert r["required_team"] in {"Medical Team","Fire Team","Rescue Team","Food/Water Team","General Team"}
    print(r)
print("AI fallback tests passed.")
