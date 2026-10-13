"""Secure server-side creation of Student records.

The Vue "Add Student" modal posts here instead of opening Frappe
Desk. Every rule (required fields, e-mail format, duplicates,
which roles may create) is enforced on the backend.
"""

import json
from contextlib import contextmanager

import frappe

from backend_custom_app.api.permissions import (
    STUDENT_ROLE,
    STAFF_ROLES,
    require_staff,
)

# Fields accepted from the modal, grouped the way the modal shows them.
SCALAR_FIELDS = (
    "first_name",
    "middle_name",
    "last_name",
    "student_email_id",
    "student_mobile_number",
    "joining_date",
    "date_of_birth",
    "gender",
    "blood_group",
    "nationality",
    "address_line_1",
    "address_line_2",
    "city",
    "state",
    "pincode",
    "country",
    "student_applicant",
    "enabled",
    "branch",
    "standard",
    "division",
)

ACADEMIC_FIELDS = (
    "program",
    "academic_year",
    "academic_term",
    "student_batch_name",
    "student_category",
)

# Optional field: add the new student to this Student Group in the same call.
OPTIONAL_GROUP_FIELD = "student_group"


def _clean(payload, key):
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _valid_email(email):
    if "@" not in email:
        return False
    local, _, domain = email.partition("@")
    return bool(local) and "." in domain and domain.rsplit(".", 1)[-1]


@contextmanager
def _elevated_for_customer_write():
    """Grant temporary write permissions for doctypes Education creates.

    Education's ``Student.on_update`` creates (or updates) an ERPNext
    ``Customer`` record with ``.insert()``/``.save()`` **without**
    ``ignore_permissions``. Education's ``Student.validate_user`` also
    creates the portal ``User`` and calls ``add_roles`` (which saves
    without ``ignore_permissions``) before its later ignored save.
    ``ProgramEnrollment.on_submit`` creates ``Course Enrollment`` docs
    the same way. Our staff callers normally lack the Accounts / Core /
    Education permissions those controllers assume, so we temporarily
    seed the request-scoped ``role_permissions`` cache for those
    doctypes only.

    We must never call ``frappe.set_user()`` for this: it rewrites
    ``local.session.sid`` and wipes the CSRF token (``local.session.data``),
    which broke every later unsafe request from the same browser session
    (the "Invalid Request" CSRFTokenError). ``frappe.local`` is
    greenlet-local per request, so the grant never leaks to another user.

    Note: ``User.on_update`` calls ``frappe.clear_cache(user=...)``,
    which resets ``frappe.local.role_permissions`` entirely. Callers
    must pre-create any needed portal User (see
    ``_precreate_portal_user``) *before* entering this block so
    ``validate_user`` skips its save.
    """

    user = frappe.session.user
    if not user or user == "Administrator":
        yield
        return

    grants = {}
    for doctype in ("Customer", "User", "Course Enrollment"):
        # Seed both the non-owner and owner cache variants: during
        # ``Document.insert`` the permission check runs after
        # ``set_user_and_timestamp``, so the new doc's ``owner`` is the
        # calling staff user and ``get_doc_permissions`` looks up the
        # ``(doctype, user, True)`` key.
        for is_owner in (False, True):
            key = (doctype, user, is_owner)
            saved = frappe.local.role_permissions.get(key)
            existing = dict(frappe.local.role_permissions.get(key) or {})
            existing.update(
                {"select": 1, "read": 1, "write": 1, "create": 1, "report": 1}
            )
            frappe.local.role_permissions[key] = existing
            grants[key] = saved

    try:
        yield
    finally:
        for key, saved in grants.items():
            if saved is None:
                frappe.local.role_permissions.pop(key, None)
            else:
                frappe.local.role_permissions[key] = saved


def _precreate_portal_user(first_name, last_name, email, gender=None):
    """Create the portal User *before* the Student insert, if missing.

    Education's ``Student.validate_user`` creates this same User (Website
    type, no password, no welcome mail) and then calls ``add_roles`` which
    saves the User **without** ``ignore_permissions``. ``User.on_update``
    calls ``frappe.clear_cache(user=...)``, which resets
    ``frappe.local.role_permissions`` and wipes any request-scoped grant
    we seeded for the subsequent Customer insert. By creating the User
    ourselves first (with ``ignore_permissions``), ``validate_user`` sees
    an existing User and skips its save entirely, so the Customer grant
    survives. ``_ensure_portal_user`` then adds the Student role and
    back-fills ``Student.user`` after the Student is saved.
    """

    email = (email or "").strip()
    if not email or frappe.db.exists("User", email):
        return email if email else None

    user = frappe.get_doc(
        {
            "doctype": "User",
            "first_name": first_name or email.split("@")[0],
            "last_name": last_name or "",
            "email": email,
            "gender": gender or None,
            "send_welcome_email": 0,
            "user_type": "Website User",
        }
    )
    user.flags.ignore_permissions = True
    user.insert(ignore_permissions=True)
    return user.name


def _ensure_portal_user(student):
    """Guarantee the Student is linked to a User that can reach the portal.

    The Education controller only creates + links a User when no account
    exists for the e-mail *yet*. When an account already existed (or the
    controller skipped user creation) ``Student.user`` can be left empty
    or point at an account that never received the Student role. Both
    cases produce "not a student portal" (``is_student_portal()`` false)
    after creation.

    - If the e-mail has no User at all: do nothing (the controller is the
      single owner of credential creation; we never invent passwords).
    - If the User is a staff / administrator account: never hijack it.
    - Otherwise: ensure the User has the Student role and back-fill the
      ``Student.user`` link when it is empty.
    """

    email = (student.student_email_id or "").strip()
    if not email:
        return student.user

    # Re-read the freshest link (the controller may have written it during
    # insert even though the in-memory doc is stale).
    linked = student.user or frappe.db.get_value(
        "Student", student.name, "user"
    )

    if not frappe.db.exists("User", email):
        return linked

    user = frappe.db.get_value(
        "User", email, ["name", "enabled"], as_dict=True
    )

    roles = set(frappe.get_roles(user.name))
    if roles.intersection(STAFF_ROLES):
        return linked

    if STUDENT_ROLE not in roles:
        _add_student_role(user.name)

    if not linked:
        frappe.db.set_value("Student", student.name, "user", user.name)
        linked = user.name

    return linked


def _add_student_role(user):
    """Append the Student role to a User without touching the session."""

    doc = frappe.get_doc("User", user)
    if any(r.role == STUDENT_ROLE for r in doc.roles):
        return
    doc.append("roles", {"role": STUDENT_ROLE})
    doc.save(ignore_permissions=True)


@frappe.whitelist(methods=["POST"])
def create_student(payload=None):
    """Create a real Frappe Student (staff only)."""

    require_staff()

    if isinstance(payload, str):
        try:
            data = json.loads(payload)
        except ValueError:
            frappe.throw("Invalid request payload.")
    else:
        data = payload or {}

    if not isinstance(data, dict):
        frappe.throw("Invalid request payload.")

    # --------------------------------------------------
    # Required + validated fields
    # --------------------------------------------------

    first_name = _clean(data, "first_name")
    email = _clean(data, "student_email_id")

    if not first_name:
        frappe.throw("First Name is required.", title="Missing field")

    if not email:
        frappe.throw(
            "Student Email Address is required.", title="Missing field"
        )

    if not _valid_email(email):
        frappe.throw(
            "Please enter a valid Student Email Address.",
            title="Invalid email",
        )

    if frappe.db.exists("Student", {"student_email_id": email}):
        frappe.throw(
            "A student with this email address already exists.",
            title="Duplicate student",
        )

    # --------------------------------------------------
    # Student document
    # --------------------------------------------------

    student = frappe.new_doc("Student")

    for fieldname in SCALAR_FIELDS:
        if frappe.get_meta("Student").has_field(fieldname):
            value = _clean(data, fieldname)
            if value is not None:
                student.set(fieldname, value)

    student.first_name = first_name
    student.student_email_id = email
    student.enabled = 1 if data.get("enabled", 1) in (1, "1", True) else 0

    if not student.joining_date:
        student.joining_date = frappe.utils.today()

    # --------------------------------------------------
    # Date-of-Birth guard
    # --------------------------------------------------

    dob = _clean(data, "date_of_birth")
    if dob:
        dob_date = frappe.utils.getdate(dob)
        today = frappe.utils.getdate()
        if dob_date >= today:
            frappe.throw(
                "Date of Birth must be strictly before today.",
                title="Invalid Date of Birth",
            )
        student.date_of_birth = dob

    # --------------------------------------------------
    # Pre-create the portal User (if missing) BEFORE the Student
    # insert. Education's validate_user would otherwise create it and
    # its User.on_update cache wipe would break the Customer grant below.
    # --------------------------------------------------

    _precreate_portal_user(
        first_name, _clean(data, "last_name"), email, _clean(data, "gender")
    )

    # Link an existing portal user, when the e-mail already has one.
    if frappe.db.exists("User", email):
        student.user = email

    # --------------------------------------------------
    # Guardians (created as real Guardian records)
    # --------------------------------------------------

    for row in data.get("guardians") or []:
        if not isinstance(row, dict):
            continue

        guardian_name = (row.get("guardian_name") or "").strip()
        if not guardian_name:
            continue

        guardian = frappe.new_doc("Guardian")
        guardian.guardian_name = guardian_name

        if row.get("mobile_number"):
            guardian.mobile_number = row.get("mobile_number")
        if row.get("email_address"):
            guardian.email_address = row.get("email_address")
        if row.get("relation"):
            guardian.relation = row.get("relation")

        guardian.insert(ignore_permissions=True)

        student.append(
            "guardians",
            {
                "guardian": guardian.name,
                "guardian_name": guardian.guardian_name,
                "relation": row.get("relation") or "",
            },
        )

    # --------------------------------------------------
    # Save (the Education controller also creates the Customer)
    # --------------------------------------------------

    # Use request-scoped permission seeding — never frappe.set_user(),
    # which corrupts the caller's session (sid + CSRF token).
    with _elevated_for_customer_write():
        student.insert(ignore_permissions=True)

    # --------------------------------------------------
    # Guarantee the Student is linked to a usable portal login
    # --------------------------------------------------

    linked_user = _ensure_portal_user(student)

    # --------------------------------------------------
    # Optional Student Group enrollment
    #
    # Education's StudentGroup.validate_students() refuses to save a
    # batch/course-based group member unless the student already has a
    # *submitted* Program Enrollment matching the group's program,
    # academic year, batch and course. So we resolve the target group
    # first, derive the enrollment from it, submit that enrollment,
    # and only then append the student to the group.
    # --------------------------------------------------

    group_name = _clean(data, OPTIONAL_GROUP_FIELD)
    group_doc = None

    if group_name:
        if not frappe.db.exists("Student Group", group_name):
            frappe.throw(
                f"Student Group '{group_name}' does not exist.",
                title="Invalid Student Group",
            )
        group_doc = frappe.get_doc("Student Group", group_name)

    # --------------------------------------------------
    # Optional academic enrollment (submitted)
    # --------------------------------------------------

    enrollment = None

    program = _clean(data, "program") or (group_doc.program if group_doc else None)
    academic_year = _clean(data, "academic_year") or (
        group_doc.academic_year if group_doc else None
    )
    academic_term = _clean(data, "academic_term") or (
        group_doc.academic_term if group_doc else None
    )
    student_batch = _clean(data, "student_batch_name") or (
        group_doc.batch if group_doc else None
    )
    student_category = _clean(data, "student_category") or (
        group_doc.student_category if group_doc else None
    )

    if program and academic_year:
        enrollment = frappe.new_doc("Program Enrollment")
        enrollment.student = student.name
        enrollment.student_name = student.student_name
        enrollment.program = program
        enrollment.academic_year = academic_year
        enrollment.enrollment_date = (
            _clean(data, "enrollment_date") or frappe.utils.today()
        )

        for fieldname, value in (
            ("academic_term", academic_term),
            ("student_batch_name", student_batch),
            ("student_category", student_category),
        ):
            if value:
                enrollment.set(fieldname, value)

        enrollment.flags.ignore_permissions = True
        enrollment.insert()
        # Submitted enrollments are what Education's batch/course
        # membership check queries against (docstatus=1).
        enrollment.flags.ignore_permissions = True
        enrollment.submit()

    # Now that any required enrollment is submitted, append to the group.
    added_to_group = None

    if group_doc:
        already_in = frappe.db.exists(
            "Student Group Student",
            {
                "parent": group_name,
                "parenttype": "Student Group",
                "student": student.name,
            },
        )
        if not already_in:
            group_doc.append(
                "students",
                {"student": student.name, "student_name": student.student_name},
            )
            group_doc.save(ignore_permissions=True)
            added_to_group = group_name

    return {
        "success": True,
        "student": student.name,
        "student_name": student.student_name,
        "student_email_id": student.student_email_id,
        "linked_user": linked_user,
        "enrollment": enrollment.name if enrollment else None,
        "student_group": added_to_group,
        "message": f"{student.student_name} was added successfully.",
    }
