# RescueLens — Generative AI Emergency Report Prioritization MVP

RescueLens is a hackathon MVP that converts unstructured citizen emergency messages into structured, prioritized information for human rescue operators.

## Core workflow

Citizen Report → Gemini Generative AI → Structured Triage → Priority → Supabase → Rescue Dashboard → Map / Responder Assignment

## Included MVP features

1. Citizen SOS emergency report
2. Gemini Generative AI triage
3. Emergency classification
4. Information extraction
5. AI-generated summary and reasoning
6. Critical / High / Medium / Low priority
7. Urdu / English / Roman Urdu understanding through the AI prompt
8. Supabase PostgreSQL storage
9. Rescue command dashboard
10. Emergency map using Leaflet + OpenStreetMap
11. Responder availability and assignment
12. ETA estimate
13. Status flow: Waiting → Dispatched → In Progress → Resolved
14. Deterministic safety fallback if Gemini is unavailable

## Technology stack

- Python
- Streamlit
- Google Gemini API (Generative AI)
- Supabase PostgreSQL
- Folium / Leaflet
- OpenStreetMap
- Git / GitHub
- Streamlit Community Cloud

## Run locally

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt
copy .env.example .env   # Windows
# or: cp .env.example .env

streamlit run app.py
```

The app works locally without Supabase by using a small SQLite fallback. For the hackathon's target architecture, configure Gemini + Supabase.

## Gemini setup

1. Open Google AI Studio.
2. Create an API key.
3. Put it in `.env`:

```env
GEMINI_API_KEY=your_key
GEMINI_MODEL=gemini-3.5-flash-lite
```

Never commit the real key to GitHub.

## Supabase setup

1. Create a Supabase project.
2. Open SQL Editor.
3. Run `supabase_schema.sql`.
4. Copy the project URL and anon/publishable key.
5. Add:

```env
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your_key
```

## Deploy

Push the folder to GitHub, then create a new Streamlit Community Cloud app and select the repository, branch, and `app.py`.

In Streamlit Cloud, add the secrets in the app's Secrets settings:

```toml
GEMINI_API_KEY = "your_key"
GEMINI_MODEL = "gemini-3.5-flash-lite"
SUPABASE_URL = "https://your-project.supabase.co"
SUPABASE_KEY = "your_key"
COMMAND_CENTER_PASSWORD = "choose-a-password"   # optional: locks the Command Center
```

Do not put secrets in the repository.

## Hackathon demo

Use this test report:

> Heavy flood water has entered our house. Six people are trapped inside and one elderly person is unconscious. We need immediate rescue.

Expected demonstration:

1. Citizen submits report.
2. Gemini analyzes the message.
3. The system produces a Critical emergency.
4. AI summary appears.
5. Report is saved in Supabase.
6. Command Center shows it at the top of the queue.
7. Map shows the location.
8. A responder can be assigned.
9. Status can move to Dispatched, In Progress, and Resolved.

## Scope limits

This is a decision-support MVP, not an autonomous emergency dispatch or medical diagnosis system. It does not replace trained responders. Real emergency-service integrations, nationwide deployment, advanced computer vision, drone/satellite systems, and other large-scale features are outside this MVP.

## Cost-conscious design

The project is designed around free/open-source tools and available free tiers. API, hosting, map-tile, and database limits can change, so check the current provider limits before a live demo.
