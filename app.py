import hmac
import os
import time
from datetime import datetime, timezone
from io import BytesIO
from xml.sax.saxutils import escape as xml_escape

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

# Must run before ai_service / db are imported (they read the environment).
load_dotenv()

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate,
    Table,
    TableStyle,
    Paragraph,
    Spacer,
)

from ai_service import triage_emergency
from db import (
    USE_SUPABASE,
    init_db,
    create_emergency,
    list_emergencies,
    get_stats,
    seed_responders,
    list_responders,
    assign_responder,
    update_status,
)
from map_utils import render_map, reverse_geocode, parse_geolocation_result
from streamlit_js_eval import get_geolocation
from features_ui import (
    render_agent_corner,
    render_agent_command_center,
    render_nearby_services,
)


load_dotenv()

st.set_page_config(
    page_title="RescueLens",
    page_icon="🚨",
    layout="wide",
)



@st.cache_resource(show_spinner=False)
def _startup():
    """
    Create tables and demo responders ONCE per server process. This used to
    run on every click/rerun (about 24 Supabase requests each time).
    """
    init_db()
    seed_responders()
    return True


try:
    _startup()
except Exception:
    st.error(
        "RescueLens could not connect to its database. Check SUPABASE_URL / "
        "SUPABASE_KEY in .env (or Streamlit secrets) and make sure "
        "supabase_schema.sql was run in your Supabase project."
    )
    st.stop()


def _password_required():
    return bool(os.getenv("COMMAND_CENTER_PASSWORD", "").strip())


def _is_unlocked():
    """True if no Command Center password is set, or it was already entered."""
    return (not _password_required()) or bool(
        st.session_state.get("command_unlocked")
    )


def _show_unlock_form():
    """
    Optional operator login. Set COMMAND_CENTER_PASSWORD in .env (or Streamlit
    secrets) to protect the Command Center; leave it empty for no password.
    """
    st.subheader("Emergency Command Center")
    st.info("This area is for authorised operators. Enter the password to continue.")

    entered = st.text_input("Password", type="password", key="command_password")

    if st.button("UNLOCK", key="command_unlock"):
        expected = os.getenv("COMMAND_CENTER_PASSWORD", "").strip()

        if hmac.compare_digest(entered.encode(), expected.encode()):
            st.session_state["command_unlocked"] = True
            st.rerun()
        else:
            st.error("Incorrect password.")


def priority_rank(p):
    return {
        "Critical": 0,
        "High": 1,
        "Medium": 2,
        "Low": 3,
    }.get(p, 4)


# =============================================================
# PDF REPORT GENERATOR
# =============================================================

def SafeParagraph(text, style):
    """
    Paragraph for text that comes from users or the database. reportlab reads
    <b>, <font> ... as markup, so text such as "<b" in a name or location used
    to crash the whole report. Escaping makes it print literally.
    """
    return Paragraph(xml_escape(str(text)), style)


def generate_rescue_report_pdf(reports):
    buffer = BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=25,
        leftMargin=25,
        topMargin=25,
        bottomMargin=25,
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "ReportTitle",
        parent=styles["Title"],
        alignment=TA_CENTER,
        fontSize=20,
        leading=24,
        spaceAfter=6,
    )

    subtitle_style = ParagraphStyle(
        "ReportSubtitle",
        parent=styles["Normal"],
        alignment=TA_CENTER,
        fontSize=9,
        leading=12,
        spaceAfter=12,
    )

    cell_style = ParagraphStyle(
        "CellStyle",
        parent=styles["Normal"],
        fontSize=7,
        leading=9,
    )

    header_style = ParagraphStyle(
        "HeaderStyle",
        parent=cell_style,
        fontName="Helvetica-Bold",
        textColor=colors.white,
        alignment=TA_CENTER,
    )

    story = []

    # ---------------------------------------------------------
    # TITLE
    # ---------------------------------------------------------

    story.append(
        Paragraph(
            "RescueLens",
            title_style,
        )
    )

    story.append(
        Paragraph(
            "Government Rescue Service Report",
            subtitle_style,
        )
    )

    generated_time = datetime.now().strftime(
        "%d %B %Y, %I:%M %p"
    )

    story.append(
        Paragraph(
            f"Generated on: {generated_time}",
            cell_style,
        )
    )

    story.append(Spacer(1, 12))

    # ---------------------------------------------------------
    # SUMMARY STATISTICS
    # ---------------------------------------------------------

    total_cases = len(reports)

    total_people = sum(
        int(r.get("people_affected") or 0)
        for r in reports
    )

    critical_count = sum(
        r.get("priority") == "Critical"
        for r in reports
    )

    high_count = sum(
        r.get("priority") == "High"
        for r in reports
    )

    medium_count = sum(
        r.get("priority") == "Medium"
        for r in reports
    )

    low_count = sum(
        r.get("priority") == "Low"
        for r in reports
    )

    resolved_count = sum(
        r.get("status") == "Resolved"
        for r in reports
    )

    active_count = total_cases - resolved_count

    summary_data = [
        [
            "TOTAL CASES",
            "PEOPLE AFFECTED",
            "CRITICAL",
            "HIGH",
            "MEDIUM",
            "LOW",
            "ACTIVE",
            "RESOLVED",
        ],
        [
            str(total_cases),
            str(total_people),
            str(critical_count),
            str(high_count),
            str(medium_count),
            str(low_count),
            str(active_count),
            str(resolved_count),
        ],
    ]

    summary_table = Table(
        summary_data,
        colWidths=[
            65,
            90,
            60,
            55,
            65,
            55,
            60,
            65,
        ],
    )

    summary_table.setStyle(
        TableStyle(
            [
                (
                    "BACKGROUND",
                    (0, 0),
                    (-1, 0),
                    colors.HexColor("#1f2937"),
                ),
                (
                    "TEXTCOLOR",
                    (0, 0),
                    (-1, 0),
                    colors.white,
                ),
                (
                    "BACKGROUND",
                    (0, 1),
                    (-1, 1),
                    colors.HexColor("#f3f4f6"),
                ),
                (
                    "FONTNAME",
                    (0, 0),
                    (-1, 0),
                    "Helvetica-Bold",
                ),
                (
                    "FONTNAME",
                    (0, 1),
                    (-1, 1),
                    "Helvetica-Bold",
                ),
                (
                    "FONTSIZE",
                    (0, 0),
                    (-1, -1),
                    7,
                ),
                (
                    "ALIGN",
                    (0, 0),
                    (-1, -1),
                    "CENTER",
                ),
                (
                    "VALIGN",
                    (0, 0),
                    (-1, -1),
                    "MIDDLE",
                ),
                (
                    "GRID",
                    (0, 0),
                    (-1, -1),
                    0.5,
                    colors.grey,
                ),
                (
                    "TOPPADDING",
                    (0, 0),
                    (-1, -1),
                    7,
                ),
                (
                    "BOTTOMPADDING",
                    (0, 0),
                    (-1, -1),
                    7,
                ),
            ]
        )
    )

    story.append(summary_table)

    story.append(Spacer(1, 18))

    # ---------------------------------------------------------
    # RECORDS TITLE
    # ---------------------------------------------------------

    story.append(
        Paragraph(
            "Emergency Service Records",
            styles["Heading2"],
        )
    )

    story.append(Spacer(1, 8))

    # ---------------------------------------------------------
    # REPORT TABLE
    # ---------------------------------------------------------

    table_data = [
        [
            Paragraph("Emergency", header_style),
            Paragraph("Reporter", header_style),
            Paragraph("Location", header_style),
            Paragraph("People", header_style),
            Paragraph("Severity", header_style),
            Paragraph("Emergency Type", header_style),
            Paragraph("Rescue Team", header_style),
            Paragraph("Status", header_style),
            Paragraph("Date / Time", header_style),
        ]
    ]

    for r in reports:

        assignment = r.get("assignment") or {}

        responder_name = assignment.get(
            "responder_name",
            "Not assigned",
        )

        created_at = r.get("created_at") or ""

        if created_at:
            try:
                dt = datetime.fromisoformat(
                    created_at.replace("Z", "+00:00")
                )

                created_at = dt.strftime(
                    "%d-%m-%Y %H:%M"
                )

            except Exception:
                pass

        table_data.append(
            [
                SafeParagraph(
                    f"#{r.get('display_number', '')}",
                    cell_style,
                ),

                SafeParagraph(
                    str(
                        r.get("reporter_name")
                        or "Anonymous"
                    ),
                    cell_style,
                ),

                SafeParagraph(
                    str(
                        r.get("location")
                        or "Not provided"
                    ),
                    cell_style,
                ),

                SafeParagraph(
                    str(
                        r.get("people_affected")
                        or 0
                    ),
                    cell_style,
                ),

                SafeParagraph(
                    str(
                        r.get("priority")
                        or "Unknown"
                    ),
                    cell_style,
                ),

                SafeParagraph(
                    str(
                        r.get("emergency_type")
                        or "Unknown"
                    ),
                    cell_style,
                ),

                SafeParagraph(
                    str(responder_name),
                    cell_style,
                ),

                SafeParagraph(
                    str(
                        r.get("status")
                        or "Unknown"
                    ),
                    cell_style,
                ),

                SafeParagraph(
                    str(created_at),
                    cell_style,
                ),
            ]
        )

    report_table = Table(
        table_data,
        repeatRows=1,
        colWidths=[
            55,
            75,
            90,
            45,
            55,
            75,
            90,
            80,
            80,
        ],
    )

    report_table.setStyle(
        TableStyle(
            [
                (
                    "BACKGROUND",
                    (0, 0),
                    (-1, 0),
                    colors.HexColor("#111827"),
                ),
                (
                    "TEXTCOLOR",
                    (0, 0),
                    (-1, 0),
                    colors.white,
                ),
                (
                    "FONTNAME",
                    (0, 0),
                    (-1, 0),
                    "Helvetica-Bold",
                ),
                (
                    "FONTSIZE",
                    (0, 0),
                    (-1, -1),
                    7,
                ),
                (
                    "GRID",
                    (0, 0),
                    (-1, -1),
                    0.4,
                    colors.grey,
                ),
                (
                    "VALIGN",
                    (0, 0),
                    (-1, -1),
                    "MIDDLE",
                ),
                (
                    "ALIGN",
                    (0, 0),
                    (0, -1),
                    "CENTER",
                ),
                (
                    "ALIGN",
                    (3, 1),
                    (3, -1),
                    "CENTER",
                ),
                (
                    "TOPPADDING",
                    (0, 0),
                    (-1, -1),
                    5,
                ),
                (
                    "BOTTOMPADDING",
                    (0, 0),
                    (-1, -1),
                    5,
                ),
            ]
        )
    )

    story.append(report_table)

    story.append(Spacer(1, 15))

    story.append(
        Paragraph(
            "This report summarizes emergency cases and "
            "rescue response activity recorded by the "
            "RescueLens system.",
            cell_style,
        )
    )

    doc.build(story)

    buffer.seek(0)

    return buffer.getvalue()


# =============================================================
# MAIN APPLICATION
# =============================================================

def render_report_page(selected, responders, reports_all):
    """
    Full-page view of ONE emergency. Opened by the "View" button in the
    Priority Queue and closed with the BACK button. The report content below
    is the same panel that used to be shown under the map.
    """

    if st.button("← BACK TO COMMAND CENTER", key="back_to_command"):
        st.session_state["selected_id"] = None
        st.rerun()

    st.markdown(
        f"### Emergency #{selected['display_number']}"
    )

    x1, x2, x3 = st.columns(3)

    x1.metric("Priority", selected["priority"])
    x2.metric("Type", selected["emergency_type"])
    x3.metric("People", selected["people_affected"])

    st.markdown("**AI Summary**")
    st.info(
        selected["ai_summary"]
        or "No AI summary available."
    )

    if selected.get("ai_generated"):
        st.caption("Triage by Gemini AI, checked by the built-in safety rules.")
    else:
        st.caption("Triage by the built-in safety rules (AI was unavailable for this report).")

    if selected.get("ai_reasoning"):
        with st.expander("AI reasoning"):
            st.write(selected["ai_reasoning"])

    st.markdown("**Extracted Information**")

    q1, q2, q3, q4 = st.columns(4)

    q1.write(
        f"Trapped: "
        f"{'Yes' if selected['people_trapped'] else 'No'}"
    )
    q2.write(
        f"Medical: "
        f"{'Yes' if selected['medical_emergency'] else 'No'}"
    )
    q3.write(
        f"Critical injury: "
        f"{'Yes' if selected['critical_injury'] else 'No'}"
    )
    q4.write(
        f"Team: {selected['required_team']}"
    )

    st.markdown("**Citizen report**")
    st.write(selected["description"])

    st.caption(
        f"Location: "
        f"{selected['location'] or 'Not provided'} · "
        f"{selected['latitude']:.5f}, "
        f"{selected['longitude']:.5f}"
    )

    # =====================================================
    # RESPONSE STATUS
    # =====================================================

    st.markdown("**Response status**")

    statuses = [
        "Waiting for responder",
        "Dispatched",
        "In Progress",
        "Resolved",
    ]

    current_index = (
        statuses.index(selected["status"])
        if selected["status"] in statuses
        else 0
    )

    new_status = st.selectbox(
        "Status",
        statuses,
        index=current_index,
        key=f"status_{selected['id']}",
    )

    if st.button(
        "UPDATE STATUS",
        key=f"status_btn_{selected['id']}",
    ):
        update_status(selected["id"], new_status)
        st.success("Status updated.")
        st.rerun()

    # =====================================================
    # RESPONDER ASSIGNMENT
    # =====================================================

    if selected["status"] != "Resolved":

        st.markdown("**Responder assignment**")

        available = [
            x
            for x in responders
            if int(x.get("available", 0)) == 1
        ]

        # Only available responders from the AI-required team
        # are offered. For example, a Fire emergency only shows
        # available Fire Teams. A resolved task releases that
        # responder in db.update_status(), making it selectable
        # for the next emergency.
        assignment_pool = [
            x
            for x in available
            if x["type"] == selected["required_team"]
        ]

        if assignment_pool:
            options = {
                f"{x['name']} · {x['type']}": x["id"]
                for x in assignment_pool
            }

            chosen = st.selectbox(
                "Responder",
                list(options.keys()),
                key=f"resp_{selected['id']}",
            )

            if st.button(
                "ASSIGN RESPONDER",
                key=f"assign_{selected['id']}",
            ):
                result = assign_responder(
                    selected["id"],
                    options[chosen],
                )

                if result["ok"]:
                    st.success(
                        f"Assigned "
                        f"{result['responder']['name']} · "
                        f"{result['distance_km']:.1f} km · ETA "
                        f"{result['eta_minutes']:.0f} min"
                    )
                    st.rerun()
                else:
                    st.error(result["error"])
        else:
            st.warning(
                f"No available {selected['required_team']} found. "
                "The team will appear here when one becomes available."
            )

    # =====================================================
    # EXISTING ASSIGNMENT
    # =====================================================

    if selected.get("assignment"):
        ass = selected["assignment"]

        st.success(
            f"Assigned: {ass['responder_name']} · "
            f"{ass['distance_km']:.1f} km · ETA "
            f"{ass['eta_minutes']:.0f} min"
        )

    # =========================================================
    # NEARBY EMERGENCY SERVICES (additional feature)
    # =========================================================

    st.divider()
    render_nearby_services(selected)

    # =========================================================
    # LIVE DISASTER MAP (same map as the Command Center)
    # =========================================================

    st.divider()

    st.markdown("### Live Disaster Map")
    st.caption("Active emergency locations and responder positions.")

    render_map(reports_all, responders, selected)

    # =========================================================
    # AI EMERGENCY RESPONSE AGENT (additional feature)
    # Floating button in the bottom-right corner of this page.
    # =========================================================

    render_agent_corner(selected)


def main():

    st.markdown(
        """
        <style>

        .block-container {
            padding-top: 1.5rem;
            padding-bottom: 2rem;
        }

        .brand {
            font-size: 2rem;
            font-weight: 800;
            letter-spacing: -0.04em;
        }

        .sub {
            color: #6b7280;
            margin-top: -12px;
            margin-bottom: 20px;
        }

        .critical {
            border-left: 5px solid #dc2626;
            padding: 12px;
            background: rgba(220,38,38,.08);
        }

        .high {
            border-left: 5px solid #f59e0b;
            padding: 12px;
            background: rgba(245,158,11,.08);
        }

        .medium {
            border-left: 5px solid #eab308;
            padding: 12px;
            background: rgba(234,179,11,.08);
        }

        /* Compact emergency queue cards */
        [class*="st-key-queue_critical_"] {
            border: 2px solid #dc2626 !important;
            border-radius: 12px !important;
        }

        [class*="st-key-queue_high_"] {
            border: 2px solid #f59e0b !important;
            border-radius: 12px !important;
        }

        [class*="st-key-queue_normal_"] {
            border: 2px solid #16a34a !important;
            border-radius: 12px !important;
        }

        .queue-card-marker {
            display: none;
        }

        /* The queue itself: 3 cards per row on desktop. */
        @media (min-width: 901px) {
            div[data-testid="stHorizontalBlock"]:has(.queue-card-marker) {
                gap: 0.8rem !important;
            }
        }

        /* On narrow/mobile screens, keep two queue cards per row.
           This selector is scoped to the queue row only. */
        @media (max-width: 900px) {
            div[data-testid="stHorizontalBlock"]:has(.queue-card-marker) {
                flex-wrap: wrap !important;
                gap: 0.65rem !important;
            }

            div[data-testid="stHorizontalBlock"]:has(.queue-card-marker) > div[data-testid="stColumn"] {
                flex: 0 0 calc(50% - 0.35rem) !important;
                min-width: calc(50% - 0.35rem) !important;
                width: calc(50% - 0.35rem) !important;
            }
        }

        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="brand">RescueLens</div>',
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="sub">'
        'Generative-AI emergency report prioritization & coordination'
        '</div>',
        unsafe_allow_html=True,
    )

    # =========================================================
    # EMERGENCY REPORT PAGE
    # "View" in the Priority Queue opens the report as its own page.
    # =========================================================

    open_id = st.session_state.get("selected_id")

    if open_id and _is_unlocked():
        all_reports = list_emergencies()
        open_report = next(
            (r for r in all_reports if r["id"] == open_id),
            None,
        )

        if open_report:
            render_report_page(open_report, list_responders(), all_reports)
            return

        # The report no longer exists - fall back to the dashboard.
        st.session_state["selected_id"] = None

    command, citizen = st.tabs(
        [
            "COMMAND CENTER",
            "CITIZEN SOS",
        ]
    )

    # =========================================================
    # CITIZEN SOS
    # =========================================================

    with citizen:

        st.subheader("Report an Emergency")

        if "geo_status" not in st.session_state:
            st.session_state.geo_status = None
        if "geo_attempt" not in st.session_state:
            st.session_state.geo_attempt = 0

        detect_clicked = st.button(
            "📍 Use My Current Location",
            key="detect_location_btn",
        )

        if detect_clicked:
            st.session_state.geo_attempt += 1
            st.session_state.geo_status = "requesting"

            # Forget the previous reading, so that a failed retry can never
            # leave an old location sitting in the form.
            for _key in (
                "detected_lat",
                "detected_lon",
                "detected_address",
                "detected_accuracy",
            ):
                st.session_state.pop(_key, None)

        if st.session_state.geo_status == "requesting":

            with st.spinner("Requesting location permission..."):

                geo = get_geolocation(
                    component_key=f"rescuelens_geo_{st.session_state.geo_attempt}"
                )

            # parse_geolocation_result() understands every answer the browser
            # component can give (including errors), so this can no longer
            # hang or crash on an unexpected result.
            parsed = parse_geolocation_result(geo)

            if parsed["status"] == "success":

                with st.spinner("Finding your address..."):
                    address = reverse_geocode(
                        parsed["latitude"], parsed["longitude"]
                    )

                st.session_state.detected_lat = parsed["latitude"]
                st.session_state.detected_lon = parsed["longitude"]
                st.session_state.detected_accuracy = parsed["accuracy"]
                # If the address lookup is offline, use the coordinates so the
                # Location field is never left empty. Kept within the form's
                # 200-character limit so a long address cannot block the report.
                st.session_state.detected_address = (
                    address
                    or f"{parsed['latitude']:.5f}, {parsed['longitude']:.5f}"
                )[:200]
                st.session_state.geo_status = "success"

            elif parsed["status"] != "waiting":
                st.session_state.geo_status = parsed["status"]

        if st.session_state.geo_status == "requesting":
            st.info(
                "Waiting for your browser. If a location prompt appears, "
                "choose **Allow**. Nothing is sent until you submit the report."
            )
        elif st.session_state.geo_status == "success":
            _lat = st.session_state.detected_lat
            _lon = st.session_state.detected_lon
            _coords_text = f"{_lat:.5f}, {_lon:.5f}"
            _address = st.session_state.get("detected_address")
            _accuracy = st.session_state.get("detected_accuracy")

            _message = "📍 Location detected automatically: "
            if _address and _address != _coords_text:
                _message += f"{_address} ({_coords_text})"
            else:
                _message += _coords_text

            if _accuracy:
                _message += (
                    f" · accuracy about {_accuracy / 1000:.1f} km"
                    if _accuracy >= 1000
                    else f" · accuracy about {_accuracy:.0f} m"
                )

            st.success(_message)

            if _accuracy and _accuracy > 2000:
                st.warning(
                    "This looks like an approximate location (this device has "
                    "no GPS, so the browser guessed from the network). Please "
                    "check the address below and correct it if needed."
                )
        elif st.session_state.geo_status == "denied":
            st.error(
                "Location permission was denied. Please allow location access "
                "in your browser settings to detect your location automatically, "
                "or enter your location manually below."
            )
        elif st.session_state.geo_status == "unavailable":
            st.warning(
                "Location services (GPS) appear to be disabled or unavailable. "
                "Please enable location services on your device, or enter your "
                "location manually below."
            )
        elif st.session_state.geo_status == "timeout":
            st.warning(
                "Location request timed out. Please try again, or enter your "
                "location manually below."
            )
        elif st.session_state.geo_status == "failed":
            st.warning(
                "Could not detect your location automatically. Please try again, "
                "or enter your location manually below."
            )

        with st.form(
            "sos_form",
            clear_on_submit=True,
        ):

            description = st.text_area(
                "Emergency description",
                placeholder=(
                    "Example: Flood water has entered our house. "
                    "Six people are trapped and one person is unconscious."
                ),
                height=140,
            )

            c1, c2 = st.columns(2)

            with c1:

                location = st.text_input(
                    "Location / address",
                    value=st.session_state.get("detected_address") or "",
                    placeholder="e.g. Bahawalpur, Punjab",
                )

                lat = st.number_input(
                    "Latitude",
                    value=st.session_state.get("detected_lat", 29.3956),
                    format="%.6f",
                )

            with c2:

                people = st.number_input(
                    "People affected",
                    min_value=1,
                    max_value=500,
                    value=1,
                )

                lon = st.number_input(
                    "Longitude",
                    value=st.session_state.get("detected_lon", 71.6836),
                    format="%.6f",
                )

            c3, c4 = st.columns(2)

            with c3:

                name = st.text_input(
                    "Reporter name (optional)"
                )

            with c4:

                phone = st.text_input(
                    "Reporter phone (optional)"
                )

            submitted = st.form_submit_button(
                "SEND EMERGENCY REPORT",
                type="primary",
                use_container_width=True,
            )

        if submitted:

            problems = []

            if len(description.strip()) < 10:
                problems.append(
                    "Please provide a more detailed emergency description."
                )

            if len(description) > 2000:
                problems.append(
                    "The description is too long (maximum 2000 characters)."
                )

            if len(location) > 200 or len(name) > 100 or len(phone) > 30:
                problems.append(
                    "Location (200), name (100) or phone (30) is too long."
                )

            if not (-90 <= float(lat) <= 90) or not (-180 <= float(lon) <= 180):
                problems.append(
                    "Latitude must be between -90 and 90, and longitude "
                    "between -180 and 180."
                )

            # Simple per-session limit so the public form cannot be used to
            # burn through the Gemini quota.
            wait = 15 - (time.time() - st.session_state.get("last_report_ts", 0))

            if not problems and wait > 0:
                problems.append(
                    f"Please wait {int(wait) + 1} seconds before sending "
                    "another report."
                )

            if problems:

                for message in problems:
                    st.error(message)

            else:

                try:

                    with st.spinner(
                        "Analyzing emergency with Generative AI..."
                    ):

                        triage = triage_emergency(
                            description,
                            int(people),
                        )

                    emergency = create_emergency(
                        description=description.strip(),
                        location=location.strip(),
                        latitude=float(lat),
                        longitude=float(lon),
                        people_affected=int(people),
                        reporter_name=name.strip() or None,
                        reporter_phone=phone.strip() or None,
                        triage=triage,
                    )

                except Exception:

                    st.error(
                        "Your report could not be saved right now. Please try "
                        "again in a moment. If this is an emergency, contact "
                        "your local emergency services directly."
                    )
                    st.caption("Your text (copy it so you do not lose it):")
                    st.code(description.strip(), language=None)

                else:

                    st.session_state["last_report_ts"] = time.time()

                    st.success(
                        f"Emergency #{emergency['display_number']} submitted."
                    )

                    st.info(
                        f"AI result: **{triage['emergency_type']}** · "
                        f"**{triage['priority']}** · "
                        f"{triage['required_team']}"
                    )

                    st.write(
                        triage["summary"]
                    )

                    if (
                        float(lat) == 29.3956
                        and float(lon) == 71.6836
                        and not st.session_state.get("detected_lat")
                    ):
                        st.warning(
                            "No location was detected or entered, so the "
                            "default Bahawalpur coordinates were used. Use "
                            "'Use My Current Location' or type the latitude "
                            "and longitude next time."
                        )

    # =========================================================
    # COMMAND CENTER
    # =========================================================

    with command:

        if not _is_unlocked():
            _show_unlock_form()
            st.stop()

        st.subheader(
            "Emergency Command Center"
        )

        st.caption(
            "Database: Supabase (cloud)"
            if USE_SUPABASE
            else "Database: local SQLite file (not shared, and lost if a cloud host restarts)"
        )

        stats = get_stats()

        a, b, c, d, e = st.columns(5)

        a.metric(
            "Total",
            stats["total"],
        )

        b.metric(
            "Critical",
            stats["critical"],
        )

        c.metric(
            "High",
            stats["high"],
        )

        d.metric(
            "Medium",
            stats["medium"],
        )

        e.metric(
            "Resolved",
            stats["resolved"],
        )

        st.divider()

        # =====================================================
        # DATA
        # =====================================================

        reports_all = list_emergencies()
        responders = list_responders()

        # =====================================================
        # AI EMERGENCY RESPONSE AGENT
        # Available straight from the Command Center (no need to
        # open a report with View first).
        # =====================================================

        render_agent_command_center(reports_all)

        st.divider()

        # =====================================================
        # PRIORITY QUEUE
        # =====================================================

        st.markdown("### Priority Queue")

        filter_value = st.selectbox(
            "Filter",
            [
                "All",
                "Critical",
                "High",
                "Medium",
                "Low",
                "Resolved",
            ],
            key="queue_filter",
        )

        reports = sorted(
            reports_all,
            key=lambda x: (
                x["status"] == "Resolved",   # resolved cases go last
                priority_rank(x["priority"]),
                x["created_at"],
            ),
        )

        if filter_value != "All":
            reports = [
                r
                for r in reports
                if r["priority"] == filter_value
                or r["status"] == filter_value
            ]

        if not reports:
            st.info("No reports found.")
        else:
            # One row of 3 columns per 3 reports, so the cards read in priority
            # order left-to-right, top-to-bottom (also in the 2-per-row mobile
            # layout). The marker inside each card lets the responsive CSS
            # target ONLY the queue rows.
            queue_row = None
            for i, r in enumerate(reports):
                if i % 3 == 0:
                    queue_row = st.columns(3)
                col = queue_row[i % 3]
                with col:
                    priority = str(r.get("priority") or "Medium").lower()
                    if r.get("status") == "Resolved":
                        card_class = "normal"
                    elif priority == "critical":
                        card_class = "critical"
                    elif priority == "high":
                        card_class = "high"
                    else:
                        card_class = "normal"

                    with st.container(
                        border=True,
                        key=f"queue_{card_class}_{r['id']}",
                    ):
                        st.markdown(
                            f'<span class="queue-card-marker {card_class}"></span>',
                            unsafe_allow_html=True,
                        )

                        st.markdown(
                            f"**#{r['display_number']} · {r['priority']}**"
                        )

                        st.caption(
                            f"{r['emergency_type']} · {r['status']}"
                        )

                        summary = (
                            r.get("ai_summary")
                            or r.get("description", "")[:150]
                        )
                        st.write(summary[:180])

                        if st.button(
                            "View",
                            key=f"view_{r['id']}",
                            use_container_width=True,
                        ):
                            st.session_state["selected_id"] = r["id"]
                            st.rerun()

        st.divider()

        # =========================================================
        # LIVE DISASTER MAP
        # =========================================================

        st.markdown("### Live Disaster Map")
        st.caption(
            "Active emergency locations and responder positions. "
            "Select a report from the queue to inspect its details."
        )

        selected_id = st.session_state.get("selected_id")

        selected = next(
            (r for r in reports_all if r["id"] == selected_id),
            None,
        )

        render_map(
            reports_all,
            responders,
            selected,
        )

        st.divider()

        # =========================================================
        # SELECTED EMERGENCY REPORT
        # =========================================================

        st.info(
            "Select an emergency from the Priority Queue to inspect "
            "its AI analysis, responder assignment, and status."
        )

        # =========================================================
        # GOVERNMENT RESCUE REPORT
        # =========================================================

        st.divider()

        with st.expander(
            "GOVERNMENT RESCUE REPORT",
            expanded=False,
        ):

            st.markdown(
                "### Rescue Service Report"
            )

            st.caption(
                "Generate a complete record of rescue "
                "operations recorded in RescueLens."
            )

            # -----------------------------------------------------
            # REPORT DATA
            # -----------------------------------------------------

            report_data = []

            for r in reports_all:

                assignment = (
                    r.get("assignment")
                    or {}
                )

                responder_name = assignment.get(
                    "responder_name",
                    "Not assigned",
                )

                created_at = (
                    r.get("created_at")
                    or ""
                )

                if created_at:

                    try:

                        dt = datetime.fromisoformat(
                            created_at.replace(
                                "Z",
                                "+00:00",
                            )
                        )

                        created_at = dt.strftime(
                            "%d-%m-%Y %H:%M"
                        )

                    except Exception:

                        pass

                report_data.append(
                    {
                        "Emergency": (
                            f"#{r.get('display_number', '')}"
                        ),
                        "Reporter": (
                            r.get("reporter_name")
                            or "Anonymous"
                        ),
                        "Location": (
                            r.get("location")
                            or "Not provided"
                        ),
                        "People Affected": (
                            r.get("people_affected")
                            or 0
                        ),
                        "Severity": (
                            r.get("priority")
                            or "Unknown"
                        ),
                        "Emergency Type": (
                            r.get("emergency_type")
                            or "Unknown"
                        ),
                        "Rescue Team": (
                            responder_name
                        ),
                        "Status": (
                            r.get("status")
                            or "Unknown"
                        ),
                        "Date / Time": (
                            created_at
                        ),
                    }
                )

            # -----------------------------------------------------
            # SHOW REPORT
            # -----------------------------------------------------

            if report_data:

                df_report = pd.DataFrame(
                    report_data
                )

                total_people_report = int(
                    df_report[
                        "People Affected"
                    ].sum()
                )

                resolved_report = int(
                    (
                        df_report["Status"]
                        == "Resolved"
                    ).sum()
                )

                active_report = int(
                    (
                        df_report["Status"]
                        != "Resolved"
                    ).sum()
                )

                rc1, rc2, rc3, rc4 = st.columns(4)

                rc1.metric(
                    "Total Cases",
                    len(df_report),
                )

                rc2.metric(
                    "People Affected",
                    total_people_report,
                )

                rc3.metric(
                    "Resolved",
                    resolved_report,
                )

                rc4.metric(
                    "Active Cases",
                    active_report,
                )

                st.markdown(
                    "#### Emergency Records"
                )

                st.dataframe(
                    df_report,
                    use_container_width=True,
                    hide_index=True,
                )

                # -------------------------------------------------
                # PDF DOWNLOAD
                # -------------------------------------------------

                pdf_data = (
                    generate_rescue_report_pdf(
                        reports_all
                    )
                )

                st.download_button(
                    label="DOWNLOAD PDF REPORT",
                    data=pdf_data,
                    file_name=(
                        "RescueLens_Government_"
                        "Rescue_Report.pdf"
                    ),
                    mime="application/pdf",
                    type="primary",
                    use_container_width=True,
                )

            else:

                st.info(
                    "No emergency records are available "
                    "to generate a report."
                )


if __name__ == "__main__":
    main()