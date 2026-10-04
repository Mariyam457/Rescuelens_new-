# RescueLens - setup, new features and fixes

## Run it (Windows PowerShell, inside this folder)
    python -m pip install -r requirements.txt
    python check_features.py          # checks Gemini, the agent and nearby services
    python test_fixes.py              # offline regression tests (no keys needed)
    python -m streamlit run app.py    # or double-click run_app.bat

`.env` already contains your Gemini and Supabase values. Keep it OUT of GitHub.

## Before the first run with Supabase (one time)
1. Supabase dashboard -> SQL Editor -> paste and run `supabase_schema.sql`.
2. Start the app. It creates the 10 demo responders in Supabase on first start.
3. The Command Center shows "Database: Supabase (cloud)" when it is connected.
   If it says "local SQLite", the Supabase values were not picked up.

## Streamlit Cloud (instead of uploading .env)
Settings -> Secrets:
    GEMINI_API_KEY = "..."
    GEMINI_MODEL = "gemini-3.5-flash-lite"
    SUPABASE_URL = "..."
    SUPABASE_KEY = "..."
    COMMAND_CENTER_PASSWORD = "choose-a-password"
Main file path: rescuelens_mvp_gps_update/rescuelens_mvp/app.py (nested folder).

## Features
- "View" opens the emergency on its own page (BACK TO COMMAND CENTER returns).
- AI Emergency Response Agent: floating "AI AGENT" button, bottom-right of that page.
- Nearby Emergency Services & Resources (OpenStreetMap, no key) on that page.

## Fixes applied (from the audit)
Reliability / bugs
- .env is now loaded before the database module reads it (Supabase was silently ignored locally).
- Database setup/seeding runs once per server, not on every click (~24 Supabase calls saved per click).
- Dashboard map no longer reruns the whole app on every pan/zoom.
- Report creation retries if two reports arrive at once, and shows a friendly message (keeping the
  citizen's text) if saving fails. A database outage shows a clear message instead of a traceback.
- PDF export no longer crashes on text such as "<b" or "<font" in names/locations.
- check_features.py used an invalid team name; fixed.
Security
- All citizen / OpenStreetMap text is escaped in map popups, tooltips and the PDF (stored-XSS).
- Optional Command Center password (COMMAND_CENTER_PASSWORD). Citizens can still file reports.
- Form validation (length limits, latitude/longitude range) and a 15-second per-session cooldown.
- Triage prompt now has a prompt-injection guard.
AI
- Standard model everywhere: gemini-3.5-flash-lite.
- If Gemini rejects the `temperature` setting, triage and the agent retry without it automatically.
- A placeholder key no longer triggers failing API calls on every submit.
- Triage keywords now match whole words ("diesel" is no longer Medical; "fireworks"/"fire drill" are
  no longer Critical Fire), include Urdu script, understand "water has entered", and read spelled-out
  counts ("six people"). Real emergencies were re-tested and stay Critical/High.
- The agent can raise but never lower severity below the triage priority, and sees the assigned responder.
UX
- The report page says whether triage came from Gemini or the built-in rules, and shows the AI reasoning.
- Queue: resolved cases sort last; High priority cards are amber; resolved cards are green.
- A warning appears if the default Bahawalpur coordinates were used because no location was given.
- Dependencies now have upper version bounds; reportlab added.

## Not changed (needs a decision or cannot be verified here)
Row-level-security hardening, citizen tracking pages, saving agent output to the database,
auto-refresh, an Urdu PDF font, undo/confirm on Resolve, and Gemini lowering keyword-based priority.
