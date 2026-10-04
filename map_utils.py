import math
from html import escape as _esc
import requests
import streamlit as st
import folium
from folium.plugins import Fullscreen
from streamlit_folium import st_folium


PRIORITY_COLORS = {
    "Critical": "red",
    "High": "orange",
    "Medium": "blue",
    "Low": "green",
}


def fetch_route(start_lat, start_lon, end_lat, end_lon):
    """Return OSRM route geometry, distance and ETA."""
    try:
        url = (
            "https://router.project-osrm.org/route/v1/driving/"
            f"{start_lon},{start_lat};{end_lon},{end_lat}"
            "?overview=full&geometries=geojson"
        )

        response = requests.get(
            url,
            timeout=6
        )

        response.raise_for_status()

        data = response.json()

        route = data["routes"][0]

        return {
            "geometry": route["geometry"]["coordinates"],
            "distance_km": route["distance"] / 1000,
            "eta_minutes": route["duration"] / 60,
        }

    except Exception:
        return None


def reverse_geocode(lat, lon):
    """Return a human-readable address in English."""
    try:
        url = "https://nominatim.openstreetmap.org/reverse"

        response = requests.get(
            url,
            params={
                "lat": lat,
                "lon": lon,
                "format": "json",
                "addressdetails": 1,
                "accept-language": "en",
            },
            headers={
                "User-Agent": "RescueLens-Emergency-App/1.0",
                "Accept-Language": "en",
            },
            timeout=6,
        )

        response.raise_for_status()

        data = response.json()

        return data.get("display_name")

    except Exception:
        return None


def _to_float(value):
    """float(value), or None if it is missing / not a finite number."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def parse_geolocation_result(geo):
    """
    Turn whatever streamlit_js_eval.get_geolocation() returned into a plain
    result, so the Citizen SOS page can never get stuck on an unexpected shape.

    Returns a dict with a "status":
        "waiting"      geo is None - the browser has not answered yet
        "success"      plus "latitude", "longitude" and "accuracy" (metres or None)
        "denied"       permission refused (browser error code 1)
        "unavailable"  GPS / network location not available (code 2)
        "timeout"      the browser took too long (code 3)
        "failed"       any other / unrecognised answer
    """
    if geo is None:
        return {"status": "waiting"}

    if isinstance(geo, dict):
        err = geo.get("error")

        if err:
            code = None
            message = ""

            if isinstance(err, dict):
                code = err.get("code")
                message = str(err.get("message") or "")
            else:
                message = str(err)

            try:
                code = int(code)
            except (TypeError, ValueError):
                code = None

            if code is None:
                text = message.lower()
                if "denied" in text or "permission" in text:
                    code = 1
                elif "unavailable" in text or "not supported" in text:
                    code = 2
                elif "timeout" in text or "timed out" in text:
                    code = 3

            return {"status": {1: "denied", 2: "unavailable", 3: "timeout"}.get(code, "failed")}

        coords = geo.get("coords") if isinstance(geo.get("coords"), dict) else geo

        lat = _to_float(coords.get("latitude"))
        lon = _to_float(coords.get("longitude"))

        if lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180:
            return {
                "status": "success",
                "latitude": lat,
                "longitude": lon,
                "accuracy": _to_float(coords.get("accuracy")),
            }

    return {"status": "failed"}


def render_map(reports, responders, selected=None):

    # Default location
    center = [29.3956, 71.6836]

    # Center map on selected emergency
    if selected:
        center = [
            selected["latitude"],
            selected["longitude"]
        ]

    # Center map on first report
    elif reports:
        center = [
            reports[0]["latitude"],
            reports[0]["longitude"]
        ]

    # Create map
    m = folium.Map(
        location=center,
        zoom_start=12,
        control_scale=True
    )

    # Add fullscreen button
    Fullscreen().add_to(m)

    # ==============================
    # ADD RESPONDERS
    # ==============================

    for responder in responders:

        folium.Marker(
            [
                responder["latitude"],
                responder["longitude"]
            ],

            tooltip=(
                f"{_esc(str(responder['name']))} · "
                f"{_esc(str(responder['type']))}"
            ),

            popup=(
                f"<b>{_esc(str(responder['name']))}</b><br>"
                f"{_esc(str(responder['type']))}<br>"
                f"Available: "
                f"{'Yes' if responder['available'] else 'No'}"
            ),

            icon=folium.Icon(
                color=(
                    "green"
                    if responder["available"]
                    else "gray"
                ),
                icon="user"
            ),

        ).add_to(m)

    # ==============================
    # ADD EMERGENCY REPORTS
    # ==============================

    for r in reports:

        color = PRIORITY_COLORS.get(
            r["priority"],
            "gray"
        )

        # Citizen-written text goes into HTML popups: always escape it.
        popup = (
            f"<b>#{r['display_number']} · "
            f"{_esc(str(r['priority']))}</b><br>"

            f"{_esc(str(r['emergency_type']))}<br>"

            f"{_esc(str(r['ai_summary'] or r['description'][:100]))}<br>"

            f"Status: {_esc(str(r['status']))}"
        )

        folium.Marker(
            [
                r["latitude"],
                r["longitude"]
            ],

            tooltip=(
                f"#{r['display_number']} · "
                f"{_esc(str(r['priority']))}"
            ),

            popup=folium.Popup(
                popup,
                max_width=320
            ),

            icon=folium.Icon(
                color=color,
                icon="warning-sign"
            ),

        ).add_to(m)

        # ==============================
        # DRAW ROUTE TO SELECTED REPORT
        # ==============================

        assignment = r.get("assignment")

        if (
            assignment
            and selected
            and selected.get("id") == r.get("id")
        ):

            responder = next(
                (
                    x
                    for x in responders
                    if x["id"]
                    == assignment.get("responder_id")
                ),
                None
            )

            if responder:

                route = fetch_route(
                    responder["latitude"],
                    responder["longitude"],
                    r["latitude"],
                    r["longitude"],
                )

                if route:

                    # Convert GeoJSON coordinates
                    # [longitude, latitude]
                    # to Folium coordinates
                    # [latitude, longitude]

                    points = [
                        [lat, lon]
                        for lon, lat
                        in route["geometry"]
                    ]

                    folium.PolyLine(
                        points,
                        weight=5,
                        opacity=0.85,
                        tooltip="OSRM driving route"
                    ).add_to(m)

                    assignment["distance_km"] = (
                        route["distance_km"]
                    )

                    assignment["eta_minutes"] = (
                        route["eta_minutes"]
                    )

    # Display map in Streamlit

    # returned_objects=[]: nothing here reads the map's pan/zoom/click state,
    # so do not rerun the whole app (and re-query the database) on every move.
    st_folium(
        m,
        height=520,
        use_container_width=True,
        key="rescue_map",
        returned_objects=[],
        
    )