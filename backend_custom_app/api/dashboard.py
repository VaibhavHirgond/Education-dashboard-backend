"""Admin dashboard aggregation endpoints.

Staff-only summaries for the administrator dashboard. All figures are
computed from persisted Education/ERPNext records:

- ``Student`` (enabled flag) for enrolled student counts
- ``Instructor`` (status=Active) for teacher counts
- ``Student Attendance`` (submitted) for student presence/absence and trends
- ``Sales Invoice`` (submitted, student-linked) for fee balances
- ``Payment Entry`` allocations against those invoices for collections
- Frappe HR ``Attendance`` (submitted, employee-linked instructors) for
  teacher presence and absence

Teacher attendance is derived from the HR ``Attendance`` doctype for the
Employee linked on each active Instructor record; instructors without a
linked Employee are simply not counted as present or absent.
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
    "Teacher attendance is read from HR Employee Attendance records. "
    "Instructors without a linked Employee are not counted."
)


@frappe.whitelist(methods=["GET"])
def get_admin_dashboard(
    academic_year: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
):
    """Return aggregated admin dashboard metrics for one reporting period.

    ``academic_year`` is optional; when omitted the period comes from
    Education Settings (current academic year), falling back to the
    academic year containing today, then to the calendar year.

    ``start_date`` / ``end_date`` (inclusive) override the academic-year
    period when both are supplied; they must be valid dates with
    start_date <= end_date.
    """
    require_staff()

    period = _resolve_period(academic_year, start_date, end_date)

    return {
        "period": period,
        "students": _students_summary(period),
        "teachers": _teachers_summary(period),
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


def _parse_date_strict(value, label: str):
    """Parse a user-supplied date, rejecting empty or malformed values."""

    raw = (value or "").strip()

    if not raw:
        frappe.throw(
            f"{label} is required when filtering by date range.",
            frappe.ValidationError,
        )

    try:
        return getdate(raw)
    except Exception:
        frappe.throw(
            f"{label} is not a valid date.",
            frappe.ValidationError,
        )


def _resolve_period(
    academic_year: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    # Explicit inclusive date range supplied by the dashboard filters.
    if (start_date or "").strip() or (end_date or "").strip():
        start = _parse_date_strict(start_date, "Start date")
        end = _parse_date_strict(end_date, "End date")

        if start > end:
            frappe.throw(
                "Start date cannot be later than end date.",
                frappe.ValidationError,
            )

        return {
            "academic_year": None,
            "from_date": start.isoformat(),
            "to_date": end.isoformat(),
            "label": "Selected period",
            "source": "custom",
        }

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


def _students_summary(period: dict) -> dict:
    # All-time total: the size of the school is not date-filtered.
    total = frappe.db.count("Student", {"enabled": 1})

    # Submitted Student Attendance rows inside the selected period.
    # A student is counted absent only when an Absent record exists.
    rows = frappe.db.sql(
        """
        SELECT student, status, COUNT(*) AS records
        FROM `tabStudent Attendance`
        WHERE docstatus = 1
          AND date BETWEEN %s AND %s
          AND status IN ('Present', 'Absent')
        GROUP BY student, status
        """,
        (period["from_date"], period["to_date"]),
        as_dict=True,
    )

    present_students = {row.student for row in rows if row.status == "Present"}
    absent_students = {row.student for row in rows if row.status == "Absent"}
    present_records = sum(int(row.records or 0) for row in rows if row.status == "Present")
    absent_records = sum(int(row.records or 0) for row in rows if row.status == "Absent")

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
        "present": len(present_students),
        "absent": len(absent_students),
        "present_records": present_records,
        "absent_records": absent_records,
        "present_today": int(present_today or 0),
        "attendance_date": nowdate(),
    }


# ---------------------------------------------------------------------------
# Teachers / Instructors
# ---------------------------------------------------------------------------


def _instructor_employees() -> list:
    """Distinct Employee records linked from active Instructor records."""

    return frappe.get_all(
        "Instructor",
        filters={
            "status": "Active",
            "employee": ["is", "set"],
        },
        distinct=True,
        pluck="employee",
    )


def _teachers_summary(period: dict) -> dict:
    total = frappe.db.count("Instructor", {"status": "Active"})

    employees = [name for name in _instructor_employees() if name]

    empty = {
        "total": int(total or 0),
        "present": 0,
        "absent": 0,
        "present_records": 0,
        "absent_records": 0,
        "linked_employees": len(employees),
        "present_available": False,
        "note": TEACHER_ATTENDANCE_NOTE,
    }

    if not employees or not frappe.db.exists("DocType", "Attendance"):
        if not employees:
            empty["note"] = (
                "No active instructor is linked to an HR Employee record, "
                "so teacher attendance cannot be reported."
            )
        return empty

    rows = frappe.db.sql(
        """
        SELECT employee, status, COUNT(*) AS records
        FROM `tabAttendance`
        WHERE docstatus = 1
          AND employee IN %(employees)s
          AND attendance_date BETWEEN %(from_date)s AND %(to_date)s
          AND status IN ('Present', 'Absent')
        GROUP BY employee, status
        """,
        {
            "employees": tuple(employees),
            "from_date": period["from_date"],
            "to_date": period["to_date"],
        },
        as_dict=True,
    )

    present_employees = {row.employee for row in rows if row.status == "Present"}
    absent_employees = {row.employee for row in rows if row.status == "Absent"}

    return {
        "total": int(total or 0),
        "present": len(present_employees),
        "absent": len(absent_employees),
        "present_records": sum(
            int(row.records or 0) for row in rows if row.status == "Present"
        ),
        "absent_records": sum(
            int(row.records or 0) for row in rows if row.status == "Absent"
        ),
        "linked_employees": len(employees),
        "present_available": True,
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

    absence = frappe.db.sql(
        """
        SELECT COUNT(*) AS absent_records,
               COUNT(DISTINCT student) AS absent_students
        FROM `tabStudent Attendance`
        WHERE docstatus = 1
          AND status = 'Absent'
          AND date BETWEEN %s AND %s
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
        "absent_records": int(absence.absent_records or 0),
        "absent_students": int(absence.absent_students or 0),
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
