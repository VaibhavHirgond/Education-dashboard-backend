"""Let the Instructor role open the Course Schedule (timetable) desk page
and create, edit schedule entries for their own groups from the Instructor
Dashboard.

The Education app only ships Course Schedule permission rows for Academics
User, Student and Guardian, so an Instructor receives a permission error
the moment the dashboard's "Today's Classes" card (or the desk list it
links to) is opened.

This patch adds a Custom DocPerm row granting the Instructor role read,
create, write and report on Course Schedule, plus read access to the
masters the Course Schedule form links to (Student Group, Course, Room,
Program). Delete stays with the admin roles.

Group scoping stays server-side: the existing ``permission_query_conditions``
/ ``has_permission`` hooks in ``backend_custom_app.api.permissions``
(``_instructor_scope_condition`` / ``_instructor_allowed``) already limit an
instructor's Course Schedule rows to their own instructor record and their
assigned Student Groups, and the Student Group link search is scoped to
those same groups, so the grant never exposes the school-wide timetable or
other instructors' schedules. Administrators and Academics User keep their
full standard permission rows.

Everything in here is idempotent: running `bench migrate` again never
duplicates permission rows and never widens an unrelated grant.
"""

import frappe

INSTRUCTOR_ROLE = "Instructor"

# Rights an instructor needs to view the timetable and add / edit entries
# for their own groups. Delete stays with the admin roles.
COURSE_SCHEDULE_PTYPES = ("read", "create", "write", "report")

# Masters the Course Schedule form links to; without read access the Desk
# link searches come back empty for instructors.
COURSE_SCHEDULE_READ_ONLY_DOCTYPES = (
    "Student Group",
    "Course",
    "Room",
    "Program",
)


def execute():
    _ensure_role()
    _grant_course_schedule_access()
    for doctype in COURSE_SCHEDULE_READ_ONLY_DOCTYPES:
        _grant_read(doctype)
    frappe.clear_cache()


def _ensure_role():
    """Create the Instructor role on sites that do not have it yet."""

    if frappe.db.exists("Role", INSTRUCTOR_ROLE):
        return

    role = frappe.get_doc(
        {
            "doctype": "Role",
            "role_name": INSTRUCTOR_ROLE,
            "desk_access": 1,
            "is_custom": 0,
            "disabled": 0,
        }
    )
    role.flags.ignore_permissions = True
    role.insert(ignore_permissions=True)


def _grant_course_schedule_access():
    """Give the Instructor role read/create/write/report on Course Schedule.

    ``add_permission`` first copies the standard DocPerm rows into Custom
    DocPerm (Frappe ignores standard perms once custom perms exist), so the
    Academics User, Student and Guardian grants stay untouched.
    """

    if not frappe.db.exists(
        "Custom DocPerm", {"parent": "Course Schedule", "role": INSTRUCTOR_ROLE}
    ):
        frappe.permissions.add_permission("Course Schedule", INSTRUCTOR_ROLE)

    for ptype in COURSE_SCHEDULE_PTYPES:
        frappe.permissions.update_permission_property(
            "Course Schedule", INSTRUCTOR_ROLE, 0, ptype, 1
        )


def _grant_read(doctype):
    """Add a read-only permission row for the Instructor role."""

    if not frappe.db.exists("DocType", doctype):
        return

    if frappe.db.exists(
        "Custom DocPerm",
        {"parent": doctype, "role": INSTRUCTOR_ROLE, "permlevel": 0, "if_owner": 0},
    ):
        return

    frappe.permissions.add_permission(doctype, INSTRUCTOR_ROLE, ptype="read")
