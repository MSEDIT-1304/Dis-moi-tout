# ==========================================================
# APPLE CALENDAR — IMPORT CALDAV ICLOUD
# ==========================================================

from datetime import date, datetime
from zoneinfo import ZoneInfo
from urllib.parse import urljoin
import xml.etree.ElementTree as ET

import requests
from icalendar import Calendar
import recurring_ical_events

from calendar_import import import_events

APPLE_CALDAV_URL = "https://caldav.icloud.com"
APPLE_CALENDAR_NAME = "Apple Calendar"
APPLE_TIME_MIN = date(1900, 1, 1)
APPLE_TIME_MAX = date(2051, 1, 1)

DAV_NS = "DAV:"
CAL_NS = "urn:ietf:params:xml:ns:caldav"
APPLE_NS = "http://apple.com/ns/ical/"

NS = {
    "d": DAV_NS,
    "c": CAL_NS,
    "a": APPLE_NS,
}


def _request(method, url, username, password, *, headers=None, body=None):
    response = requests.request(
        method,
        url,
        auth=(username, password),
        headers=headers or {},
        data=body,
        timeout=45,
    )
    if response.status_code not in (200, 207):
        raise RuntimeError(
            f"iCloud CalDAV ({response.status_code}) : {response.text[:500]}"
        )
    return response


def _propfind(url, username, password, body, depth="0"):
    return _request(
        "PROPFIND",
        url,
        username,
        password,
        headers={
            "Depth": depth,
            "Content-Type": "application/xml; charset=utf-8",
        },
        body=body,
    )


def _first_href(xml_text, property_name):
    root = ET.fromstring(xml_text)
    node = root.find(
        f".//{{{DAV_NS}}}{property_name}/{{{DAV_NS}}}href"
    )
    return node.text.strip() if node is not None and node.text else None


def _discover_calendar_home(username, password):
    props = """<?xml version="1.0" encoding="utf-8" ?>
    <d:propfind xmlns:d="DAV:">
      <d:prop><d:current-user-principal /></d:prop>
    </d:propfind>"""
    response = _propfind(
        APPLE_CALDAV_URL, username, password, props, depth="0"
    )
    principal = _first_href(response.text, "current-user-principal")
    if not principal:
        raise RuntimeError("iCloud n'a pas fourni le principal CalDAV.")

    principal_url = urljoin(response.url, principal)
    props = """<?xml version="1.0" encoding="utf-8" ?>
    <d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
      <d:prop><c:calendar-home-set /></d:prop>
    </d:propfind>"""
    response = _propfind(
        principal_url, username, password, props, depth="0"
    )
    home = _first_href(response.text, "calendar-home-set")
    if not home:
        raise RuntimeError("Impossible de trouver les agendas iCloud.")
    return urljoin(response.url, home)


def get_apple_calendars(username, password):
    """Retourne tous les calendriers CalDAV accessibles dans iCloud."""
    home_url = _discover_calendar_home(username, password)
    props = """<?xml version="1.0" encoding="utf-8" ?>
    <d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
      <d:prop>
        <d:resourcetype />
        <d:displayname />
        <d:calendar-description />
      </d:prop>
    </d:propfind>"""
    response = _propfind(home_url, username, password, props, depth="1")
    root = ET.fromstring(response.text)
    calendars = []

    for item in root.findall(".//{DAV:}response"):
        href_node = item.find("{DAV:}href")
        if href_node is None or not href_node.text:
            continue
        resource_types = item.findall(
            ".//{DAV:}resourcetype/{urn:ietf:params:xml:ns:caldav}calendar"
        )
        if not resource_types:
            continue

        name_node = item.find(".//{DAV:}displayname")
        description_node = item.find(
            ".//{urn:ietf:params:xml:ns:caldav}calendar-description"
        )
        href = urljoin(response.url, href_node.text.strip())
        name = (
            name_node.text.strip()
            if name_node is not None and name_node.text
            else "Agenda iCloud"
        )
        calendars.append({
            "id": href,
            "nom": name,
            "description": (
                description_node.text or ""
                if description_node is not None else ""
            ),
        })

    return calendars


def _calendar_objects(username, password, calendar_url):
    """Récupère les ressources ICS de l'agenda sur toute la période utile."""
    start = APPLE_TIME_MIN.strftime("%Y%m%dT000000Z")
    end = APPLE_TIME_MAX.strftime("%Y%m%dT000000Z")
    body = f"""<?xml version="1.0" encoding="utf-8" ?>
    <c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
      <d:prop><d:getetag /><c:calendar-data /></d:prop>
      <c:filter>
        <c:comp-filter name="VCALENDAR">
          <c:comp-filter name="VEVENT">
            <c:time-range start="{start}" end="{end}" />
          </c:comp-filter>
        </c:comp-filter>
      </c:filter>
    </c:calendar-query>"""
    response = _request(
        "REPORT",
        calendar_url,
        username,
        password,
        headers={
            "Depth": "1",
            "Content-Type": "application/xml; charset=utf-8",
        },
        body=body,
    )
    root = ET.fromstring(response.text)
    objects = []
    for item in root.findall(".//{DAV:}response"):
        data = item.find(
            ".//{urn:ietf:params:xml:ns:caldav}calendar-data"
        )
        if data is not None and data.text:
            objects.append(data.text)
    return objects


def _format_occurrence(component):
    start = component.get("DTSTART")
    if start is None:
        return None

    start_value = start.dt
    if isinstance(start_value, datetime):
        if start_value.tzinfo is not None:
            start_value = start_value.astimezone(
                ZoneInfo("Europe/Paris")
            ).replace(tzinfo=None)
        event_date = start_value.date().isoformat()
        event_time = start_value.strftime("%H:%M")
    elif isinstance(start_value, date):
        event_date = start_value.isoformat()
        event_time = ""
    else:
        return None

    summary = str(component.get("SUMMARY", "Sans titre"))
    description = str(component.get("DESCRIPTION", "") or "")
    location = str(component.get("LOCATION", "") or "")
    if location:
        description = (
            f"{description}\nLieu : {location}".strip()
        )

    return {
        "titre": summary or "Sans titre",
        "description": description,
        "date": event_date,
        "heure": event_time,
    }


def get_apple_events(username, password, calendar_name="Apple Calendar"):
    """Récupère les événements et développe les récurrences jusqu'en 2050."""
    events = []
    calendars = get_apple_calendars(username, password)

    for calendar in calendars:
        for raw_ics in _calendar_objects(
            username, password, calendar["id"]
        ):
            parsed = Calendar.from_ical(raw_ics)
            occurrences = recurring_ical_events.of(parsed).between(
                APPLE_TIME_MIN, APPLE_TIME_MAX
            )
            for occurrence in occurrences:
                item = _format_occurrence(occurrence)
                if item:
                    events.append(item)

    # Déduplication locale avant insertion.
    unique = {}
    for event in events:
        key = (
            event["titre"], event["date"], event["heure"],
            event["description"]
        )
        unique[key] = event
    return list(unique.values())


def sync_apple_calendar(
    username,
    password,
    famille_id,
    membre_id,
    calendar_name="Apple Calendar",
):
    total_imported = 0
    total_skipped = 0
    calendars = get_apple_calendars(username, password)

    for calendar in calendars:
        events = []
        for raw_ics in _calendar_objects(
            username, password, calendar["id"]
        ):
            parsed = Calendar.from_ical(raw_ics)
            occurrences = recurring_ical_events.of(parsed).between(
                APPLE_TIME_MIN, APPLE_TIME_MAX
            )
            for occurrence in occurrences:
                item = _format_occurrence(occurrence)
                if item:
                    events.append(item)

        source_name = (
            f"Apple | {calendar.get('nom') or 'Agenda iCloud'} "
            f"[{calendar['id']}]"
        )
        result = import_events(
            events=events,
            famille_id=famille_id,
            membre_id=membre_id,
            calendrier=source_name,
        )
        total_imported += result.get("imported", 0)
        total_skipped += result.get("skipped", 0)

    return {"imported": total_imported, "skipped": total_skipped}


def test_apple_connection(username, password):
    try:
        return bool(get_apple_calendars(username, password))
    except Exception:
        return False


def get_apple_connection_status(username, password):
    try:
        calendars = get_apple_calendars(username, password)
        return {
            "connected": True,
            "message": f"Compte iCloud connecté ({len(calendars)} agenda(s)).",
        }
    except Exception as exc:
        return {"connected": False, "message": str(exc)}


def count_apple_calendars(username, password):
    return len(get_apple_calendars(username, password))


def count_apple_events(username, password, calendar_name="Apple Calendar"):
    return len(get_apple_events(username, password, calendar_name))


def refresh_apple_calendar(
    username, password, famille_id, membre_id,
    calendar_name="Apple Calendar",
):
    return sync_apple_calendar(
        username, password, famille_id, membre_id, calendar_name
    )


def sync_apple_events_between(
    username, password, famille_id, membre_id,
    date_debut, date_fin, calendar_name="Apple Calendar",
):
    # Import complet : le calendrier Apple est importé jusqu'en 2050.
    return sync_apple_calendar(
        username, password, famille_id, membre_id, calendar_name
    )


def disconnect_apple_calendar():
    # Les identifiants ne sont pas conservés par l'application.
    return True


def get_last_apple_sync():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_apple_profile():
    return {"provider": "Apple", "calendar": APPLE_CALENDAR_NAME}


def get_apple_timezone():
    return "Europe/Paris"


def check_apple_configuration():
    return {"configured": True, "missing": []}


def is_apple_calendar_available():
    return True


MODULE_VERSION = "2.0.0"
PROVIDER_NAME = "Apple Calendar"


def get_module_status():
    return {
        "provider": PROVIDER_NAME,
        "version": MODULE_VERSION,
        "available": is_apple_calendar_available(),
    }
