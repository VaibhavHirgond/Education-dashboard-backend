"""Set up the education access model for this site.

Creates the least-privilege "Education User" role, grants read-only
access to the records the Students page needs, and seeds the master
data used by the Branch / Division / Standard filters.

Everything in here is idempotent: running `bench migrate` again will
never duplicate records or overwrite existing student data.
"""

import frappe

from backend_custom_app.api.permissions import (
    BRANCH_READ_ROLES,
    EDUCATION_READ_ONLY_DOCTYPES,
    EDUCATION_USER_ROLE,
)

# Demo master data so the filters are usable out of the box.
BRANCHES = ("Computer Science", "Information Technology")

STANDARDS = ("First Year", "Second Year")

DIVISIONS = (
    ("First Year - A", "Computer Science", "First Year"),
    ("First Year - B", "Information Technology", "First Year"),
    ("Second Year - A", "Computer Science", "Second Year"),
)


def execute():
    _ensure_role()
    _ensure_read_permissions()
    _seed_masters()
    _replace_default_signup_access()
    frappe.clear_cache()


def _ensure_role():
    """Create the least-privilege role used by the application."""

    if frappe.db.exists("Role", EDUCATION_USER_ROLE):
        return

    role = frappe.get_doc(
        {
            "doctype": "Role",
            "role_name": EDUCATION_USER_ROLE,
            "desk_access": 0,
            "is_custom": 0,
            "disabled": 0,
        }
    )
    role.flags.ignore_permissions = True
    role.insert(ignore_permissions=True)


def _ensure_read_permissions():
    """Grant read-only access to the records the application loads."""

    for doctype in EDUCATION_READ_ONLY_DOCTYPES:
        _grant_read(doctype, EDUCATION_USER_ROLE)

    # Staff roles need to read the Branch master to see it in the filters.
    for role in BRANCH_READ_ROLES:
        if role == EDUCATION_USER_ROLE:
            continue
        _grant_read("Branch", role)


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


def _seed_masters():
    """Create the branch / standard / division masters used by the filters."""

    for branch in BRANCHES:
        _insert_named("Branch", "branch", {"branch": branch})

    for standard in STANDARDS:
        _insert_named("Standard", "standard", {"standard": standard})

    for division, branch, standard in DIVISIONS:
        _insert_named(
            "Division",
            "division",
            {
                "division": division,
                "branch": branch,
                "standard": standard,
            },
        )


def _insert_named(doctype, name_field, values):
    if frappe.db.exists(doctype, values[name_field]):
        return

    doc = frappe.get_doc({"doctype": doctype, **values})
    doc.flags.ignore_permissions = True
    doc.insert(ignore_permissions=True)


def _replace_default_signup_access():
    """Accounts created by the old sign-up flow only had the broad
    'Desk User' role; swap it for the least-privilege education role."""

    rows = frappe.db.sql(
        """
        select parent
        from `tabHas Role`
        where parenttype = 'User'
            and role = 'Desk User'
        group by parent
        having count(*) = 1
        """,
        as_dict=True,
    )

    for row in rows:
        user_name = row.parent

        if user_name in ("Administrator", "Guest"):
            continue

        user = frappe.get_doc("User", user_name)
        user.set(
            "roles",
            [role for role in user.roles if role.role != "Desk User"],
        )
        user.append_roles(EDUCATION_USER_ROLE)
        user.save(ignore_permissions=True)
