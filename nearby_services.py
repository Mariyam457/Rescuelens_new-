"""
Nearby Emergency Services & Resource Finder
===========================================

Finds hospitals, fire stations, police stations, rescue/ambulance centers and
evacuation/assembly points around a point, using free OpenStreetMap services:

  * Overpass API  - to search OSM for emergency facilities
  * Nominatim     - ONLY to geocode a manually typed location (the app already
                    uses Nominatim for reverse geocoding in map_utils.py)

REQUIRED API KEYS: none. No Google Maps, no paid API, nothing to configure.

The incident location itself is NOT collected here - it comes from the
emergency record that RescueLens already stores (GPS or manual entry).

Notes:
  * Distances are straight-line (great-circle) distances from the incident.
  * Data comes from OpenStreetMap volunteers, so coverage, addresses and phone
    numbers vary by area. Always confirm with the local emergency number.
"""

import math

import requests


USER_AGENT = "RescueLens-Emergency-App/1.0"

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"

# category key -> (label, marker colour for folium)
CATEGORIES = {
    "hospital": ("🏥 Hospitals", "red"),
    "fire": ("🚒 Fire stations", "orange"),
    "police": ("🚓 Police stations", "blue"),
    "rescue": ("🚑 Rescue / emergency centers", "green"),
    "evacuation": ("📍 Evacuation / safe points", "purple"),
}

_SINGULAR = {
    "hospital": "Hospital",
    "fire": "Fire station",
    "police": "Police station",
    "rescue": "Rescue / emergency center",
    "evacuation": "Evacuation / safe point",
}


# =============================================================
# HELPERS
# =============================================================

def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlmb = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    )
    return 2 * r * math.asin(math.sqrt(a))


def valid_coordinates(lat, lon):
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return False
    return -90 <= lat <= 90 and -180 <= lon <= 180 and not (lat == 0 and lon == 0)


def directions_url(origin_lat, origin_lon, dest_lat, dest_lon):
    """Free, key-less directions link (OpenStreetMap / OSRM)."""
    return (
        "https://www.openstreetmap.org/directions"
        f"?engine=fossgis_osrm_car&route={origin_lat:.6f}%2C{origin_lon:.6f}"
        f"%3B{dest_lat:.6f}%2C{dest_lon:.6f}"
    )


def geocode_location(text):
    """
    Turn a manually typed place name into (lat, lon, display_name).
    Returns None if it cannot be found or the service is unavailable.
    """
    text = (text or "").strip()
    if not text:
        return None
    try:
        response = requests.get(
            NOMINATIM_SEARCH_URL,
            params={
                "q": text,
                "format": "json",
                "limit": 1,
                "accept-language": "en",
            },
            headers={"User-Agent": USER_AGENT, "Accept-Language": "en"},
            timeout=8,
        )
        response.raise_for_status()
        data = response.json()
        if not data:
            return None
        item = data[0]
        return float(item["lat"]), float(item["lon"]), item.get("display_name", text)
    except Exception:
        return None


def _classify(tags):
    amenity = tags.get("amenity")
    emergency = tags.get("emergency")

    if amenity == "hospital" or tags.get("healthcare") == "hospital":
        return "hospital"
    if amenity == "fire_station":
        return "fire"
    if amenity == "police":
        return "police"
    if emergency in ("ambulance_station", "rescue_station") or amenity == "ambulance_station":
        return "rescue"
    if emergency == "assembly_point":
        return "evacuation"
    return None


def _address(tags):
    if tags.get("addr:full"):
        return tags["addr:full"]

    street = " ".join(
        p for p in (tags.get("addr:housenumber"), tags.get("addr:street")) if p
    )
    parts = [
        street,
        tags.get("addr:suburb") or tags.get("addr:neighbourhood"),
        tags.get("addr:city") or tags.get("addr:town") or tags.get("addr:village"),
        tags.get("addr:district"),
    ]
    return ", ".join(p for p in parts if p)


def _build_query(lat, lon, radius_m):
    around = f"(around:{int(radius_m)},{lat:.6f},{lon:.6f})"
    selectors = [
        '["amenity"="hospital"]',
        '["healthcare"="hospital"]',
        '["amenity"="fire_station"]',
        '["amenity"="police"]',
        '["emergency"="ambulance_station"]',
        '["amenity"="ambulance_station"]',
        '["emergency"="rescue_station"]',
        '["emergency"="assembly_point"]',
    ]
    body = "\n".join(f"  nwr{around}{s};" for s in selectors)
    return f"[out:json][timeout:25];\n(\n{body}\n);\nout center tags;"


# =============================================================
# PUBLIC API
# =============================================================

def find_nearby_services(lat, lon, radius_km=5):
    """
    Search for emergency resources around (lat, lon).

    Never raises. Returns:
        {
          "ok": bool,
          "error": str | None,   # friendly message when ok is False
          "services": [ {name, category, category_label, type, distance_km,
                         address, phone, website, latitude, longitude,
                         directions_url}, ... ]   # sorted by distance
        }
    """
    try:
        if not valid_coordinates(lat, lon):
            return {
                "ok": False,
                "error": "The incident location is missing or invalid, so nearby services cannot be searched.",
                "services": [],
            }

        lat, lon = float(lat), float(lon)
        query = _build_query(lat, lon, radius_km * 1000)

        elements = None
        last_problem = None

        for endpoint in OVERPASS_ENDPOINTS:
            try:
                response = requests.post(
                    endpoint,
                    data={"data": query},
                    headers={"User-Agent": USER_AGENT},
                    timeout=30,
                )
                if response.status_code == 429:
                    last_problem = "rate_limit"
                    continue
                response.raise_for_status()
                elements = response.json().get("elements", [])
                break
            except requests.exceptions.ConnectionError:
                last_problem = "connection"
            except requests.exceptions.Timeout:
                last_problem = "timeout"
            except Exception:
                last_problem = "service"

        if elements is None:
            messages = {
                "rate_limit": "The map data service is busy (rate limit). Please wait a minute and try again.",
                "connection": "Could not connect to the internet / map data service. Please check your connection and try again.",
                "timeout": "The map data service took too long to respond. Please try again, or use a smaller search radius.",
                "service": "The map data service is temporarily unavailable. Please try again shortly.",
            }
            return {
                "ok": False,
                "error": messages.get(last_problem, messages["service"]),
                "services": [],
            }

        services = []
        seen = set()

        for el in elements:
            tags = el.get("tags") or {}
            category = _classify(tags)
            if not category:
                continue

            if el.get("type") == "node":
                s_lat, s_lon = el.get("lat"), el.get("lon")
            else:
                center = el.get("center") or {}
                s_lat, s_lon = center.get("lat"), center.get("lon")
            if s_lat is None or s_lon is None:
                continue

            # A hospital can be mapped as both a building and a site: de-dupe.
            name = (tags.get("name:en") or tags.get("name") or "").strip()
            dedupe_key = (category, name.lower(), round(s_lat, 3), round(s_lon, 3))
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            distance = haversine_km(lat, lon, s_lat, s_lon)
            if distance > radius_km:
                continue

            services.append(
                {
                    "name": name or f"Unnamed {_SINGULAR[category].lower()}",
                    "category": category,
                    "category_label": CATEGORIES[category][0],
                    "type": _SINGULAR[category],
                    "distance_km": distance,
                    "address": _address(tags),
                    "phone": tags.get("phone") or tags.get("contact:phone") or "",
                    "website": tags.get("website") or tags.get("contact:website") or "",
                    "latitude": s_lat,
                    "longitude": s_lon,
                    "directions_url": directions_url(lat, lon, s_lat, s_lon),
                }
            )

        services.sort(key=lambda s: s["distance_km"])
        return {"ok": True, "error": None, "services": services}

    except Exception as exc:
        return {
            "ok": False,
            "error": f"Nearby services could not be loaded ({type(exc).__name__}).",
            "services": [],
        }
