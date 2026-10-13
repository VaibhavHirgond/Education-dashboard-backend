"""Holiday data for the attendance calendar.

Holiday resolution order:

1. An existing, configured Frappe ``Holiday List`` (preferred). The applicable
   list is read from the active Company's ``default_holiday_list`` so an
   institution can configure its own state / local list without any code
   change. Each ``Holiday`` row carries its own date, so year-varying and
   lunar-calendar dates are handled exactly as they were entered.
2. A maintainable built-in dataset in this module (Central Government of
   India gazetted holidays for 2026), used only when no configured Frappe
   Holiday List covers the requested year.

Holidays are display-only. They never create, alter or imply any attendance
record, leave approval or attendance percentage.
"""

import frappe
from frappe.utils import getdate


# ---------------------------------------------------------------------------
# Built-in reference dataset: Central Government of India gazetted holidays
# for 2026 (Delhi / New Delhi list).
#
# Source: DoPT O.M. "Holidays to be observed in Central Government Offices
# during the year 2026" (No. 12/2/2023-JCA) and the Government of India
# National Portal holiday calendar (https://www.india.gov.in/calendar).
#
# ``status``:
#   - "confirmed": date is fixed by the official notification.
#   - "tentative": date depends on moon sighting / a later official
#     notification and may shift (per DoPT O.M. paras 5.1-5.3 for
#     Id-ul-Fitr, Id-ul-Zuha, Muharram and Id-e-Milad).
#
# This is a reference fallback only. It is NOT a claim that every state or
# institution observes these exact dates - configure a Frappe Holiday List
# for the applicable state / institution to override it.
# ---------------------------------------------------------------------------

CENTRAL_2026 = [
    ("2026-01-26", "Republic Day", "gazetted", "confirmed"),
    ("2026-03-04", "Holi", "gazetted", "confirmed"),
    ("2026-03-21", "Id-ul-Fitr", "gazetted", "tentative"),
    ("2026-03-26", "Ram Navami", "gazetted", "confirmed"),
    ("2026-03-31", "Mahavir Jayanti", "gazetted", "confirmed"),
    ("2026-04-03", "Good Friday", "gazetted", "confirmed"),
    ("2026-05-01", "Buddha Purnima", "gazetted", "confirmed"),
    ("2026-05-27", "Id-ul-Zuha (Bakrid)", "gazetted", "tentative"),
    ("2026-06-26", "Muharram", "gazetted", "tentative"),
    ("2026-08-15", "Independence Day", "gazetted", "confirmed"),
    ("2026-08-26", "Milad-un-Nabi (Id-e-Milad)", "gazetted", "tentative"),
    ("2026-09-04", "Janmashtami", "gazetted", "confirmed"),
    ("2026-10-02", "Mahatma Gandhi's Birthday", "gazetted", "confirmed"),
    ("2026-10-20", "Dussehra", "gazetted", "confirmed"),
    ("2026-11-08", "Diwali (Deepavali)", "gazetted", "confirmed"),
    ("2026-11-24", "Guru Nanak's Birthday", "gazetted", "confirmed"),
    ("2026-12-25", "Christmas Day", "gazetted", "confirmed"),
]

BUILTIN_LISTS = {"central-2026": CENTRAL_2026}


def _payload(payload):
    if isinstance(payload, str):
        try:
            data = frappe.parse_json(payload)
        except Exception:
            data = {}
    else:
        data = payload or {}

    return data if isinstance(data, dict) else {}


def _active_company():
    company = frappe.defaults.get_user_default("Company")

    if not company:
        company = frappe.db.get_single_value("Global Defaults", "default_company")

    if not company:
        company = frappe.db.get_default("company")

    if not company:
        company = frappe.db.get_value("Company", {}, "name", limit=1)

    return company


def _frappe_holiday_list_for_year(holiday_list, year):
    """Holidays from a configured Frappe Holiday List that cover ``year``."""

    if not holiday_list or not frappe.db.exists("Holiday List", holiday_list):
        return []

    rows = frappe.get_all(
        "Holiday",
        filters={"parent": holiday_list},
        fields=["holiday_date", "description", "weekly_off"],
        order_by="holiday_date asc",
        limit_page_length=400,
    )

    result = []

    for row in rows:
        if not row.holiday_date:
            continue

        date = getdate(row.holiday_date)

        if date.year != year:
            continue

        if row.weekly_off:
            # Weekly offs are handled by the calendar separately; skip so a
            # generic "Weekly Off" does not mask a real holiday name.
            continue

        result.append(
            {
                "date": date.strftime("%Y-%m-%d"),
                "name": row.description or "Holiday",
                "type": "holiday_list",
                "status": "confirmed",
                "source": "frappe_holiday_list",
            }
        )

    return result


def _builtin_holidays_for_year(year):
    """Built-in Central Government reference holidays for the given year."""

    rows = BUILTIN_LISTS.get("central-2026", [])

    result = []

    for date_str, name, holiday_type, status in rows:
        if getdate(date_str).year != year:
            continue

        result.append(
            {
                "date": date_str,
                "name": name,
                "type": holiday_type,
                "status": status,
                "source": "builtin_central_2026",
            }
        )

    return result


def _resolve_holidays(year):
    """Return (holidays, meta) for the applicable list and year."""

    company = _active_company()

    holiday_list = None

    if company and frappe.db.exists("Company", company):
        holiday_list = frappe.db.get_value(
            "Company", company, "default_holiday_list"
        )

    holidays = _frappe_holiday_list_for_year(holiday_list, year)

    if holidays:
        return holidays, {
            "source": "frappe_holiday_list",
            "holiday_list": holiday_list,
            "company": company,
            "note": "Configured Frappe Holiday List for this institution.",
        }

    # Fall back to the built-in Central Government reference dataset.
    holidays = _builtin_holidays_for_year(year)

    meta = {
        "source": "builtin_central_2026" if holidays else "none",
        "holiday_list": "",
        "company": company,
    }

    if holidays:
        meta["note"] = (
            "Central Government of India gazetted holidays for 2026 "
            "(reference). Configure a Company Holiday List to use a "
            "state-specific or institution-specific calendar."
        )
    else:
        meta["note"] = (
            "No configured Holiday List covers this year and no built-in "
            "data is available for it. Set the Company's default Holiday "
            "List to display holidays for this period."
        )

    return holidays, meta


@frappe.whitelist(methods=["GET", "POST"])
def get_holidays(payload=None):
    """Holidays for the authenticated user's institution and a given year.

    ``payload`` may contain ``year`` (defaults to the current year). Only the
    applicable, configured list is returned - a client cannot request another
    institution's list.
    """

    data = _payload(payload)

    if frappe.session.user == "Guest":
        frappe.throw("Not permitted", frappe.PermissionError)

    year = data.get("year")

    try:
        year = int(year) if year else getdate().year
    except (TypeError, ValueError):
        year = getdate().year

    holidays, meta = _resolve_holidays(year)

    return {
        "year": year,
        "holidays": holidays,
        "count": len(holidays),
        "source": meta["source"],
        "holiday_list": meta["holiday_list"],
        "company": meta["company"],
        "note": meta["note"],
    }
