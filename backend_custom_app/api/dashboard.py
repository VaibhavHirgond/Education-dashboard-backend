"""Admin dashboard aggregation endpoints.

Staff-only summaries for the administrator dashboard. All figures are
computed from persisted Education/ERPNext records:

- ``Student`` (enabled flag) for enrolled student counts
- ``Instructor`` (status=Active) for teacher counts
- ``Student Attendance`` (submitted) for student presence and trends
- ``Sales Invoice`` (submitted, student-linked) for fee balances
- ``Payment Entry`` allocations against those invoices for collections

No instructor-attendance DocType exists in the installed apps, so
teacher presence is reported as unavailable instead of being invented.
"""

from __future__ import annotations

import frappe
from frappe.utils import add_months, flt, getdate, nowdate

from backend_custom_app.api.permissions import require_staff

MONTH_ABBRS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)

TEACHER_ATTENDANCE_NOTE = (
    "Instructor attendance is not recorded in this system "
    "(no instructor attendance source is configured)."
)


@frappe.whitelist(methods=["GET"])
def get_admin_dashboard(academic_year: str | None = None):
    """Return aggregated admin dashboard metrics for one reporting period.

    ``academic_year`` is optional; when omitted the period comes from
    Education Settings (current academic year), falling back to the
    academic year containing today, then to the calendar year.
    """
    require_staff()

    period = _resolve_period(academic_year)

    return {
        "period": period,
        "students": _students_summary(),
        "teachers": _teachers_summary(),
        "fees": _fees_summary(period),
        "attendance": _attendance_summary(period),
        "charts": {
            "fees_collected": _fees_monthly(period),
            "attendance": _attendance_monthly(period),
        },
        "generated_at": nowdate(),
    }


# ---------------------------------------------------------------------------
# Period resolution
# ---------------------------------------------------------------------------


def _resolve_period(academic_year: str | None = None) -> dict:
    requested = (academic_year or "").strip()

    if requested:
        row = frappe.db.get_value(
            "Academic Year",
            requested,
            ["name", "year_start_date", "year_end_date"],
            as_dict=True,
        )
        if not row:
            frappe.throw(
                f"Academic year {requested} was not found.",
                frappe.ValidationError,
            )
        source = "requested"
    else:
        row = _current_academic_year_row()
        source = "education_settings" if row else None

    if row:
        start = getdate(row.year_start_date) if row.year_start_date else None
        end = getdate(row.year_end_date) if row.year_end_date else None
        name = row.name
    else:
        today = getdate(nowdate())
        name = str(today.year)
        start = today.replace(month=1, day=1)
        end = today.replace(month=12, day=31)
        source = "calendar_year"

    if not start or not end or start > end:
        frappe.throw(
            "The reporting period is not configured correctly. "
            "Set valid start/end dates on the academic year.",
            frappe.ValidationError,
        )

    label = (
        f"Academic Year {name}" if source != "calendar_year" else f"Calendar Year {name}"
    )

    return {
        "academic_year": name if source != "calendar_year" else None,
        "from_date": start.isoformat(),
        "to_date": end.isoformat(),
        "label": label,
        "source": source,
    }


def _current_academic_year_row():
    name = frappe.db.get_single_value("Education Settings", "current_academic_year")
    if not name:
        today = getdate(nowdate())
        name = frappe.db.get_value(
            "Academic Year",
            {
                "year_start_date": ["<=", today],
                "year_end_date": [">=", today],
            },
            "name",
        )
    if not name:
        return None

    return frappe.db.get_value(
        "Academic Year",
        name,
        ["name", "year_start_date", "year_end_date"],
        as_dict=True,
    )


# ---------------------------------------------------------------------------
# Students
# ---------------------------------------------------------------------------


def _students_summary() -> dict:
    total = frappe.db.count("Student", {"enabled": 1})

    present_today = frappe.db.sql(
        """
        SELECT COUNT(DISTINCT student)
        FROM `tabStudent Attendance`
        WHERE docstatus = 1
          AND date = %s
          AND status = 'Present'
        """,
        getdate(nowdate()),
    )[0][0]

    return {
        "total": int(total or 0),
        "present_today": int(present_today or 0),
        "attendance_date": nowdate(),
    }


# ---------------------------------------------------------------------------
# Teachers / Instructors
# ---------------------------------------------------------------------------


def _teachers_summary() -> dict:
    total = frappe.db.count("Instructor", {"status": "Active"})

    return {
        "total": int(total or 0),
        "present_today": None,
        "present_available": False,
        "note": TEACHER_ATTENDANCE_NOTE,
    }


# ---------------------------------------------------------------------------
# Fees
# ---------------------------------------------------------------------------


def _fees_summary(period: dict) -> dict:
    collected = frappe.db.sql(
        """
        SELECT COALESCE(SUM(per.allocated_amount), 0)
        FROM `tabPayment Entry` pe
        JOIN `tabPayment Entry Reference` per ON per.parent = pe.name
        JOIN `tabSales Invoice` si ON si.name = per.reference_name
        WHERE pe.docstatus = 1
          AND per.reference_doctype = 'Sales Invoice'
          AND si.docstatus = 1
          AND si.is_return = 0
          AND IFNULL(si.student, '') != ''
          AND pe.posting_date BETWEEN %s AND %s
        """,
        (period["from_date"], period["to_date"]),
    )[0][0]

    # Current open balance on student fee invoices (not period-filtered:
    # outstanding is a point-in-time balance across all submitted invoices).
    outstanding = frappe.db.sql(
        """
        SELECT COALESCE(SUM(outstanding_amount), 0)
        FROM `tabSales Invoice`
        WHERE docstatus = 1
          AND is_return = 0
          AND IFNULL(student, '') != ''
        """,
    )[0][0]

    return {
        "collected": flt(collected, 2),
        "outstanding": flt(outstanding, 2),
    }


def _fees_monthly(period: dict) -> list:
    rows = frappe.db.sql(
        """
        SELECT DATE_FORMAT(pe.posting_date, '%%Y-%%m') AS ym,
               COALESCE(SUM(per.allocated_amount), 0) AS amount
        FROM `tabPayment Entry` pe
        JOIN `tabPayment Entry Reference` per ON per.parent = pe.name
        JOIN `tabSales Invoice` si ON si.name = per.reference_name
        WHERE pe.docstatus = 1
          AND per.reference_doctype = 'Sales Invoice'
          AND si.docstatus = 1
          AND si.is_return = 0
          AND IFNULL(si.student, '') != ''
          AND pe.posting_date BETWEEN %s AND %s
        GROUP BY ym
        """,
        (period["from_date"], period["to_date"]),
        as_dict=True,
    )

    by_month = {row.ym: flt(row.amount, 2) for row in rows}

    return [
        {
            "month": month,
            "label": _month_label(month),
            "short": _month_short(month),
            "amount": by_month.get(month, 0.0),
        }
        for month in _iter_months(period["from_date"], period["to_date"])
    ]


# ---------------------------------------------------------------------------
# Attendance
# ---------------------------------------------------------------------------


def _attendance_summary(period: dict) -> dict:
    row = frappe.db.sql(
        """
        SELECT COUNT(*) AS recorded_days,
               COALESCE(SUM(present), 0) AS present_days
        FROM (
            SELECT sa.student,
                   sa.date,
                   MAX(CASE WHEN sa.status = 'Present' THEN 1 ELSE 0 END) AS present
            FROM `tabStudent Attendance` sa
            WHERE sa.docstatus = 1
              AND sa.date BETWEEN %s AND %s
            GROUP BY sa.student, sa.date
        ) daily
        """,
        (period["from_date"], period["to_date"]),
        as_dict=True,
    )[0]

    recorded = int(row.recorded_days or 0)
    present = int(row.present_days or 0)
    percentage = round((present / recorded) * 100, 1) if recorded else None

    return {
        "percentage": percentage,
        "present_days": present,
        "recorded_days": recorded,
        "available": recorded > 0,
    }


def _attendance_monthly(period: dict) -> list:
    rows = frappe.db.sql(
        """
        SELECT DATE_FORMAT(daily.d, '%%Y-%%m') AS ym,
               COUNT(*) AS recorded_days,
               COALESCE(SUM(daily.present), 0) AS present_days
        FROM (
            SELECT sa.student,
                   sa.date AS d,
                   MAX(CASE WHEN sa.status = 'Present' THEN 1 ELSE 0 END) AS present
            FROM `tabStudent Attendance` sa
            WHERE sa.docstatus = 1
              AND sa.date BETWEEN %s AND %s
            GROUP BY sa.student, sa.date
        ) daily
        GROUP BY ym
        """,
        (period["from_date"], period["to_date"]),
        as_dict=True,
    )

    by_month = {
        row.ym: {
            "recorded_days": int(row.recorded_days or 0),
            "present_days": int(row.present_days or 0),
        }
        for row in rows
    }

    series = []
    for month in _iter_months(period["from_date"], period["to_date"]):
        entry = by_month.get(
            month, {"recorded_days": 0, "present_days": 0}
        )
        recorded = entry["recorded_days"]
        present = entry["present_days"]
        percentage = round((present / recorded) * 100, 1) if recorded else None

        series.append(
            {
                "month": month,
                "label": _month_label(month),
                "short": _month_short(month),
                "percentage": percentage,
                "present_days": present,
                "recorded_days": recorded,
            }
        )

    return series


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iter_months(from_date, to_date):
    cursor = getdate(from_date).replace(day=1)
    end = getdate(to_date)

    while cursor <= end:
        yield cursor.strftime("%Y-%m")
        cursor = add_months(cursor, 1)


def _month_short(month: str) -> str:
    return MONTH_ABBRS[int(month[5:7]) - 1]


def _month_label(month: str) -> str:
    return f"{_month_short(month)} {month[:4]}"
