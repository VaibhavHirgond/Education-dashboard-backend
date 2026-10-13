"""Student leave applications (reuses the Education DocType).

``Student Leave Application`` from the original Education app is
submittable, so the lifecycle maps cleanly onto the portal:

* draft (docstatus 0)  -> Pending
* submitted (1)        -> Approved (the Education controller then
                          writes the matching Student Attendance rows)
* cancelled (2)        -> Rejected / cancelled

Nothing here touches the HR Employee Leave Application.
"""

import json
from contextlib import contextmanager

import frappe
from frappe.utils import getdate, today

from backend_custom_app.api.permissions import (
    get_instructor_students,
    get_student_record,
    is_instructor_portal,
    is_staff,
    require_education_access,
)

LEAVE_STATUS = {
    0: "Pending",
    1: "Approved",
    2: "Rejected",
}


def _require_approver(student=None):
    """Staff may approve anything; instructors only their own students."""

    user = frappe.session.user

    if not user or user == "Guest":
        frappe.throw(
            frappe._("Please login to continue."),
            frappe.PermissionError,
        )

    if is_staff():
        return

    if is_instructor_portal():
        if student and student not in get_instructor_students():
            frappe.throw(
                frappe._(
                    "You can only process leave for your own students."
                ),
                frappe.PermissionError,
            )
        return

    frappe.throw(
        frappe._(
            "You do not have permission to perform this action. "
            "Contact your administrator if you need access."
        ),
        frappe.PermissionError,
    )


@contextmanager
def _elevated_for_attendance_write():
    """Temporarily allow the DocTypes the Education on_submit hook writes.

    Submitting a ``Student Leave Application`` runs the Education
    controller's ``update_attendance()``, which queries Course Schedule
    and upserts Student Attendance *as the calling user*. We cannot
    modify the Education app, and ``frappe.set_user()`` corrupts the
    HTTP session (it resets ``local.session.sid``), so instead we seed
    the request-scoped ``role_permissions`` cache for the duration of
    the submit. ``frappe.local`` is per-request / greenlet-local, so
    this never leaks across requests and never widens access outside
    this single call. The caller must already have passed
    ``_require_approver``.

    The same applies to Frappe v16's query layer: ``get_list`` now
    validates *field* level permissions for filters (e.g.
    ``Course Schedule.schedule_date``), which the role cache above does
    not cover. We therefore also wrap ``frappe.get_list`` for the two
    DocTypes the controller reads so its read path behaves like
    ``ignore_permissions`` - scoped to exactly those DocTypes and to
    this one submit.
    """

    user = frappe.session.user
    if not user or user == "Administrator":
        yield
        return

    # Cache key shape: (doctype, user, is_owner). The doctype-level
    # has_permission path only reads `role_permissions.get(ptype)`.
    grants = {
        ("Course Schedule", user, False): {
            "select": 1,
            "read": 1,
            "report": 1,
        },
        ("Student Attendance", user, False): {
            "select": 1,
            "read": 1,
            "write": 1,
            "create": 1,
            "submit": 1,
            "report": 1,
        },
    }

    elevated_doctypes = {"Course Schedule", "Student Attendance"}
    original_get_list = frappe.get_list

    def get_list_with_access(*args, **kwargs):
        doctype = args[0] if args else kwargs.get("doctype")
        if doctype in elevated_doctypes:
            kwargs["ignore_permissions"] = True
        return original_get_list(*args, **kwargs)

    saved = {}
    for key, perms in grants.items():
        saved[key] = frappe.local.role_permissions.get(key)
        existing = dict(frappe.local.role_permissions.get(key) or {})
        existing.update(perms)
        frappe.local.role_permissions[key] = existing

    frappe.get_list = get_list_with_access

    try:
        yield
    finally:
        frappe.get_list = original_get_list
        for key, value in saved.items():
            if value is None:
                frappe.local.role_permissions.pop(key, None)
            else:
                frappe.local.role_permissions[key] = value

# Every group the caller may file leave for, used for validation.
def _resolve_student(student=None):
    """Return the student id this caller is allowed to act for."""

    if is_staff():
        if not student:
            frappe.throw("Student is required.")
        if not frappe.db.exists("Student", student):
            frappe.throw("Student not found.")
        return student

    record = get_student_record()

    if not record:
        frappe.throw(
            "No student record is linked to your account. "
            "Contact your administrator.",
            frappe.PermissionError,
        )

    if student and student != record.name:
        frappe.throw(
            "You can only apply for leave for yourself.",
            frappe.PermissionError,
        )

    return record.name


def _default_group(student):
    """Fallback group when the client did not name one.

    Only active memberships count, and the order is deterministic so a
    learner who belongs to several groups always lands on the same group
    instead of an arbitrary ``groups[0]``. The portal now always sends an
    explicit ``student_group`` (picked from :func:`get_my_groups`), so this
    path is only a safety net.
    """

    groups = frappe.get_all(
        "Student Group Student",
        filters={"student": student, "active": 1},
        pluck="parent",
        order_by="parent asc",
    )
    return groups[0] if groups else None


@frappe.whitelist()
def get_my_groups():
    """Return the signed-in learner's active Student Group memberships.

    The list is resolved only from the authenticated session's Student
    record, so a learner can never read — or be shown — another student's
    groups. An empty ``groups`` list means the account is not attached to
    any group yet; the portal surfaces that as a clear message instead of
    inventing one.
    """

    require_education_access()

    record = get_student_record()
    if not record:
        return {"student": None, "student_name": "", "groups": []}

    rows = frappe.get_all(
        "Student Group Student",
        filters={"student": record.name, "active": 1},
        fields=["parent"],
        order_by="parent asc",
    )

    groups = []
    seen = set()
    for row in rows:
        if row.parent in seen:
            continue
        seen.add(row.parent)
        display = (
            frappe.db.get_value("Student Group", row.parent, "student_group_name")
            or row.parent
        )
        groups.append(
            {
                "student_group": row.parent,
                "student_group_name": display,
            }
        )

    return {
        "student": record.name,
        "student_name": record.student_name or "",
        "groups": groups,
    }


def _serialize(row):
    payload = dict(row)
    payload["status"] = LEAVE_STATUS.get(row.get("docstatus"), "Pending")
    for key, value in list(payload.items()):
        if hasattr(value, "isoformat"):
            payload[key] = value.isoformat()
    return payload


@frappe.whitelist(methods=["POST"])
def apply_leave(payload=None):
    """File a Student Leave Application for the signed-in learner."""

    require_education_access()

    if isinstance(payload, str):
        try:
            data = json.loads(payload)
        except ValueError:
            frappe.throw("Invalid request payload.")
    else:
        data = payload or {}

    student = _resolve_student(data.get("student"))
    from_date = data.get("from_date")
    to_date = data.get("to_date")

    if not from_date or not to_date:
        frappe.throw("From Date and To Date are required.")

    if getdate(from_date) > getdate(to_date):
        frappe.throw("From Date cannot be after To Date.")

    if getdate(to_date) < getdate(today()):
        frappe.throw("Leave cannot be applied for dates in the past.")

    reason = (data.get("reason") or "").strip()
    if not reason:
        frappe.throw("Please provide a reason for the leave.")

    attendance_based_on = (
        data.get("attendance_based_on") or "Student Group"
    )
    if attendance_based_on not in ("Student Group", "Course Schedule"):
        attendance_based_on = "Student Group"

    doc = frappe.new_doc("Student Leave Application")
    doc.student = student
    doc.student_name = frappe.db.get_value("Student", student, "student_name")
    doc.from_date = from_date
    doc.to_date = to_date
    doc.reason = reason
    doc.mark_as_present = 1 if data.get("mark_as_present") in (1, "1", True) else 0
    doc.attendance_based_on = attendance_based_on

    if attendance_based_on == "Course Schedule":
        course_schedule = data.get("course_schedule")
        if not course_schedule:
            frappe.throw("Class is required for this leave type.")
        doc.course_schedule = course_schedule
    else:
        student_group = data.get("student_group") or _default_group(student)
        if not student_group:
            frappe.throw(
                "You are not attached to a Student Group yet. "
                "Ask your administrator to add you to a group."
            )

        if student not in frappe.get_all(
            "Student Group Student",
            filters={"parent": student_group},
            pluck="student",
        ):
            frappe.throw("You are not a member of the selected Student Group.")

        doc.student_group = student_group

    doc.insert(ignore_permissions=True)

    return {
        "success": True,
        "leave": doc.name,
        "status": "Pending",
        "message": "Leave application submitted for approval.",
        "application": _serialize(_load(doc.name)),
    }


def _load(name):
    return frappe.get_all(
        "Student Leave Application",
        filters={"name": name},
        fields=[
            "name",
            "student",
            "student_name",
            "from_date",
            "to_date",
            "reason",
            "mark_as_present",
            "attendance_based_on",
            "student_group",
            "course_schedule",
            "total_leave_days",
            "docstatus",
            "creation",
            "modified",
        ],
        limit_page_length=1,
    )[0]


@frappe.whitelist()
def get_leave_applications(payload=None):
    """Leave applications for the caller (own rows, or all for staff)."""

    require_education_access()

    if isinstance(payload, str):
        try:
            data = json.loads(payload)
        except ValueError:
            data = {}
    else:
        data = payload or {}

    filters = {}

    if is_staff():
        if data.get("student"):
            filters["student"] = data["student"]
        if data.get("status") in ("Pending", "Approved", "Rejected"):
            wanted = {
                "Pending": 0,
                "Approved": 1,
                "Rejected": 2,
            }[data["status"]]
            filters["docstatus"] = wanted
    elif is_instructor_portal():
        # Instructors only ever see the leave of their own students.
        allowed = get_instructor_students()

        if data.get("student"):
            if data["student"] not in allowed:
                frappe.throw(
                    "You can only view leave for your own students.",
                    frappe.PermissionError,
                )
            filters["student"] = data["student"]
        else:
            filters["student"] = ["in", allowed or ["__none__"]]

        if data.get("status") in ("Pending", "Approved", "Rejected"):
            wanted = {
                "Pending": 0,
                "Approved": 1,
                "Rejected": 2,
            }[data["status"]]
            filters["docstatus"] = wanted
    else:
        record = get_student_record()
        filters["student"] = record.name if record else "__none__"

    rows = frappe.get_all(
        "Student Leave Application",
        filters=filters,
        fields=[
            "name",
            "student",
            "student_name",
            "from_date",
            "to_date",
            "reason",
            "mark_as_present",
            "attendance_based_on",
            "student_group",
            "course_schedule",
            "total_leave_days",
            "docstatus",
            "creation",
            "modified",
        ],
        order_by="creation desc",
        limit_page_length=200,
    )

    return {
        "total": len(rows),
        "applications": [_serialize(row) for row in rows],
    }


@frappe.whitelist(methods=["POST"])
def approve_leave(name=None):
    """Approve a pending application (staff, or the owning instructor).

    Submitting the Education document marks the matching days in
    Student Attendance, so both portals stay consistent.
    """

    if not name or not frappe.db.exists("Student Leave Application", name):
        frappe.throw("Leave application not found.")

    doc = frappe.get_doc("Student Leave Application", name)
    _require_approver(doc.student)

    if doc.docstatus == 1:
        return {"success": True, "status": "Approved", "name": name}

    if doc.docstatus == 2:
        frappe.throw("This leave application was rejected.")

    # Submitting runs the Education controller's on_submit, which
    # queries Course Schedule and writes Student Attendance as the
    # calling user. Instructors do not hold SELECT on those DocTypes,
    # and frappe.set_user() corrupts the HTTP session, so we elevate
    # just this submit via the request-scoped role_permissions cache.
    # The ownership check above already ran as the caller.
    with _elevated_for_attendance_write():
        doc.flags.ignore_permissions = True
        doc.submit()

    return {
        "success": True,
        "status": "Approved",
        "name": name,
        "message": "Leave approved and attendance updated.",
    }


@frappe.whitelist(methods=["POST"])
def reject_leave(name=None):
    """Reject / cancel an application (staff, or the owning instructor)."""

    if not name or not frappe.db.exists("Student Leave Application", name):
        frappe.throw("Leave application not found.")

    doc = frappe.get_doc("Student Leave Application", name)
    _require_approver(doc.student)

    if doc.docstatus == 2:
        return {"success": True, "status": "Rejected", "name": name}

    doc.flags.ignore_permissions = True

    if doc.docstatus == 1:
        # Cancelling runs on_cancel -> cancel_attendance, which
        # reverts the Student Attendance rows as the calling user.
        with _elevated_for_attendance_write():
            doc.cancel()
    else:
        # A draft can be rejected directly; no attendance was written
        # for an unapproved application.
        frappe.db.set_value(
            "Student Leave Application", name, "docstatus", 2, update_modified=True
        )

    return {
        "success": True,
        "status": "Rejected",
        "name": name,
        "message": "Leave application rejected.",
    }
