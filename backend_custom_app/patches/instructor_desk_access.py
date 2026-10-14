"""Give the Instructor role read-only Desk access to the records its
dashboard quick links open (Student Attendance and Assessment Plan).

The ``permission_query_conditions`` / ``has_permission`` hooks in
``backend_custom_app.api.permissions`` already scope an instructor's
rows to their own student groups, so a read grant never exposes
school-wide attendance or assessments.

Everything in here is idempotent: running `bench migrate` again will
never duplicate permission rows or widen an existing grant.
"""

import frappe

INSTRUCTOR_READ_ONLY_DOCTYPES = (
    "Student Attendance",
    "Assessment Plan",
)


def execute():
    for doctype in INSTRUCTOR_READ_ONLY_DOCTYPES:
        _grant_read(doctype, "Instructor")

    frappe.clear_cache()


def _grant_read(doctype, role):
    """Add a read-only permission row for `role` on `doctype`."""

    if not frappe.db.exists("DocType", doctype):
        return

    if frappe.db.exists(
        "Custom DocPerm",
        {"parent": doctype, "role": role, "permlevel": 0, "if_owner": 0},
    ):
        return

    # add_permission() first copies the standard DocPerm rows into
    # Custom DocPerm (Frappe ignores standard perms once custom perms
    # exist), so no existing role loses access.
    frappe.permissions.add_permission(doctype, role, ptype="read")
