"""Let the Instructor role create and submit Assessment Plans in Desk.

Instructors already hold a read-only Custom DocPerm on Assessment Plan
(granted by ``instructor_desk_access``). This patch adds the write,
create and submit rights the normal Desk workflow needs, plus read
access to the masters the Assessment Plan form links to (Course,
Assessment Group, Academic Year/Term, Grading Scale, Room, Instructor).

Only the role's grants change. The group scoping stays server-side:
the existing ``has_permission`` hook in ``backend_custom_app.api.permissions``
(``_instructor_allowed``) denies every permission type - including
create - on any Assessment Plan whose ``student_group`` is not one of
the instructor's assigned groups, and the ``permission_query_conditions``
hook already limits the Student Group link search to those groups.
Administrators and Academics User keep their full permission rows.

Everything in here is idempotent: running `bench migrate` again never
duplicates permission rows and never widens an unrelated grant.
"""

import frappe

INSTRUCTOR_ROLE = "Instructor"

# Rights an instructor needs to save and submit a plan for their own
# group. Cancel/amend/delete stay with the admin roles.
ASSESSMENT_PLAN_PTYPES = ("read", "write", "create", "submit")

# Masters the Assessment Plan form links to; without read access the
# Desk link searches come back empty for instructors.
INSTRUCTOR_READ_ONLY_DOCTYPES = (
    "Course",
    "Assessment Group",
    "Academic Year",
    "Academic Term",
    "Grading Scale",
    "Room",
    "Instructor",
)


def execute():
    _ensure_role()
    _grant_assessment_plan_workflow()
    for doctype in INSTRUCTOR_READ_ONLY_DOCTYPES:
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


def _grant_assessment_plan_workflow():
    """Give the Instructor role read/write/create/submit on Assessment Plan.

    ``add_permission`` first copies the standard DocPerm rows into
    Custom DocPerm (Frappe ignores standard perms once custom perms
    exist - which they already do for Assessment Plan), so the full
    Academics User grant and every other role stay untouched.
    """

    if not frappe.db.exists("Custom DocPerm", {"parent": "Assessment Plan", "role": INSTRUCTOR_ROLE}):
        frappe.permissions.add_permission("Assessment Plan", INSTRUCTOR_ROLE)

    for ptype in ASSESSMENT_PLAN_PTYPES:
        frappe.permissions.update_permission_property(
            "Assessment Plan", INSTRUCTOR_ROLE, 0, ptype, 1
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
