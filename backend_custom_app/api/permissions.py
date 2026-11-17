"""Server-side access rules for the education application.

The frontend only hides controls; every education API re-checks
authentication and role membership on the server before it returns
any data, so a user without an education role is rejected with 403
no matter what the UI does.
"""

import frappe

# Least-privilege role assigned to users who sign up from the frontend.
EDUCATION_USER_ROLE = "Education User"

# Roles allowed to use the education application APIs.
EDUCATION_ACCESS_ROLES = (
    EDUCATION_USER_ROLE,
    "Academics User",
    "Education Manager",
    "Instructor",
    "Student",
    "Guardian",
    "System Manager",
    "Administrator",
)

# DocTypes the least-privilege role may only read (never write/create/delete).
# These are exactly the records the Students page loads over REST.
EDUCATION_READ_ONLY_DOCTYPES = (
    "Student",
    "Program Enrollment",
    "Branch",
    "Program",
    "Academic Year",
    "Academic Term",
    "Student Batch Name",
    "Student Category",
    "Student Group",
)

# Education roles that manage masters (they need read access to Branch too,
# which is an ERPNext doctype owned by the HR module).
BRANCH_READ_ROLES = ("Education User", "Academics User", "Education Manager")

# Roles that run the school: they see every student, every fee,
# every attendance row and the whole management dashboard.
STAFF_ROLES = (
    "Administrator",
    "System Manager",
    "Education Manager",
    "Academics User",
)

# Role used by learners who sign in through the portal.
STUDENT_ROLE = "Student"

# Role held by teachers who sign in through the portal.
INSTRUCTOR_ROLE = "Instructor"


def has_education_access(user=None):
    """Return True when the user holds one of the education access roles."""

    roles = set(frappe.get_roles(user or frappe.session.user))
    return bool(roles.intersection(EDUCATION_ACCESS_ROLES))


def require_education_access():
    """Reject guests and users without an education role (HTTP 403)."""

    user = frappe.session.user

    if not user or user == "Guest":
        frappe.throw(
            frappe._("Please login to access education data."),
            frappe.PermissionError,
        )

    if not has_education_access(user):
        frappe.throw(
            frappe._(
                "You do not have permission to access education data. "
                "Contact your administrator if you need access."
            ),
            frappe.PermissionError,
        )


def require_education_scope():
    """Allow staff, linked students, and linked instructors only.

    A plain ``Education User`` (for example a signup that has not yet been
    linked to a Student or Instructor record) must never receive the
    school-wide admin dataset. This is the default-deny companion to
    :func:`require_education_access`: it lets the three real portal scopes
    through and rejects everyone else.
    """

    require_education_access()

    if is_staff() or is_student_portal() or is_instructor_portal():
        return

    frappe.throw(
        frappe._(
            "Your account is not yet linked to a student or instructor "
            "record. Contact your administrator to complete your setup."
        ),
        frappe.PermissionError,
    )


# =========================================================
# STAFF / STUDENT IDENTIFICATION
# =========================================================


def is_staff(user=None):
    """True for users who manage the school (full dashboard + all records)."""

    roles = set(frappe.get_roles(user or frappe.session.user))
    return bool(roles.intersection(STAFF_ROLES))


def require_staff():
    """Reject anyone who is not school staff (HTTP 403)."""

    user = frappe.session.user

    if not user or user == "Guest":
        frappe.throw(
            frappe._("Please login to continue."),
            frappe.PermissionError,
        )

    if not is_staff(user):
        frappe.throw(
            frappe._(
                "You do not have permission to perform this action. "
                "Contact your administrator if you need access."
            ),
            frappe.PermissionError,
        )


def is_student_portal(user=None):
    """True for signed-in learners (Student role, never staff)."""

    user = user or frappe.session.user

    if not user or user == "Guest":
        return False

    roles = set(frappe.get_roles(user))

    if STUDENT_ROLE not in roles:
        return False

    return not bool(roles.intersection(STAFF_ROLES))


def get_student_record(user=None):
    """Return the Student document linked to this user (or None).

    A learner is identified through the Frappe session only: the
    ``Student.user`` field first, then ``Student.student_email_id``.
    Nothing about the student is ever taken from the client.
    """

    user = user or frappe.session.user

    if not user or user == "Guest":
        return None

    cache = getattr(frappe.local, "_education_student_record", None)
    if cache is not None:
        return cache

    fields = [
        "name",
        "student_name",
        "first_name",
        "middle_name",
        "last_name",
        "student_email_id",
        "student_mobile_number",
        "date_of_birth",
        "gender",
        "blood_group",
        "nationality",
        "enabled",
        "user",
        "customer",
        "joining_date",
    ]

    fields += [
        fieldname
        for fieldname in ("branch", "standard", "division")
        if frappe.get_meta("Student").has_field(fieldname)
    ]

    student = frappe.db.get_value(
        "Student",
        {"user": user, "enabled": 1},
        fields,
        as_dict=True,
    )

    if not student:
        student = frappe.db.get_value(
            "Student",
            {"student_email_id": user, "enabled": 1},
            fields,
            as_dict=True,
        )

    frappe.local._education_student_record = student
    return student


def student_name_for_user(user=None):
    """Student id of the linked record, or None."""

    student = get_student_record(user)
    return student.name if student else None


# =========================================================
# INSTRUCTOR IDENTIFICATION AND SCOPING
#
# An instructor portal user is a non-staff account linked to an
# active ``Instructor`` document through ``custom_instructor_email``
# (fallback: full-name match). Everything they can see is derived
# from the Student Groups that list them in the ``instructors``
# child table - never from anything the browser sends.
# =========================================================


def get_instructor_record(user=None):
    """Return the Instructor document linked to this user (or None).

    More than one Instructor can carry the same ``custom_instructor_email``
    (duplicates appear when a record is re-created), and the plain lookup
    otherwise returns whichever row the database happens to read last. That
    made the portal resolve to an unused duplicate with no student groups,
    so every dashboard count read zero. The match is therefore ordered by
    name and, when the email is shared, the record that actually teaches a
    group wins.
    """

    user = user or frappe.session.user

    if not user or user == "Guest":
        return None

    cache = getattr(frappe.local, "_education_instructor_record", None)
    if cache is not None:
        return cache

    fields = ["name", "instructor_name", "status", "custom_instructor_email"]

    matches = frappe.get_all(
        "Instructor",
        filters={"custom_instructor_email": user},
        fields=fields,
        order_by="name asc",
    )

    if not matches:
        full_name = (frappe.utils.get_fullname(user) or "").strip()

        if full_name:
            matches = frappe.get_all(
                "Instructor",
                filters={"instructor_name": full_name},
                fields=fields,
                order_by="name asc",
            )

    active = [
        row for row in matches if not row.status or row.status == "Active"
    ]

    frappe.local._education_instructor_record = _preferred_instructor_record(
        active
    )
    return frappe.local._education_instructor_record


def _preferred_instructor_record(records):
    """Choose deterministically when several instructors share one email."""

    if not records:
        return None

    if len(records) == 1:
        return records[0]

    assigned = set(
        frappe.get_all(
            "Student Group Instructor",
            filters={"instructor": ["in", [row.name for row in records]]},
            pluck="instructor",
        )
    )

    for row in records:
        if row.name in assigned:
            return row

    return records[0]


def is_instructor_portal(user=None):
    """Identify users linked to an active Instructor record who have the Instructor role."""

    user = user or frappe.session.user

    if not user or user == "Guest":
        return False

    roles = set(frappe.get_roles(user))

    # Keep true system administrators and system managers on the admin dashboard.
    if roles.intersection({"Administrator", "System Manager"}):
        return False

    if not get_instructor_record(user):
        return False

    return INSTRUCTOR_ROLE in roles



def get_instructor_groups(user=None):
    """Student group names this instructor is assigned to."""

    record = get_instructor_record(user)

    if not record:
        return []

    return frappe.get_all(
        "Student Group Instructor",
        filters={"parenttype": "Student Group", "instructor": record.name},
        pluck="parent",
    )


def get_instructor_students(user=None):
    """Student ids that belong to this instructor's groups."""

    groups = get_instructor_groups(user)

    if not groups:
        return []

    return frappe.get_all(
        "Student Group Student",
        filters={"parenttype": "Student Group", "parent": ["in", groups]},
        pluck="student",
    )


def get_instructor_courses(user=None):
    """Course names handed to this instructor on their Instructor Log.

    The ``Instructor Log`` child table on the Instructor document is the
    single source of truth for what an instructor teaches: the
    Administrator adds one row per (academic year, program, course,
    group) assignment, and both the dashboard "My Courses" cards and the
    Course permissions read those rows. Users without a linked
    instructor record get an empty list.
    """

    record = get_instructor_record(user)

    if not record:
        return []

    cache = getattr(frappe.local, "_education_instructor_courses", None)
    if cache is not None:
        return cache

    courses = frappe.get_all(
        "Instructor Log",
        filters={
            "parenttype": "Instructor",
            "parent": record.name,
            "course": ["is", "set"],
        },
        pluck="course",
        order_by="idx asc",
    )

    # Keep the log order, drop repeated assignments of one course.
    frappe.local._education_instructor_courses = list(dict.fromkeys(courses))
    return frappe.local._education_instructor_courses


def course_query_conditions(user, doctype=None):
    """``permission_query_conditions`` hook entry point for Course.

    Instructors only ever list the courses named on their own Instructor
    Log. Staff, administrators, students and guests are not instructors,
    so they fall through to the default, unrestricted Course list.
    """

    if not is_instructor_portal(user):
        return ""

    courses = get_instructor_courses(user)

    if not courses:
        # An instructor whose log carries no course row sees nothing.
        return "`tabCourse`.`name` = ''"

    values = ", ".join(
        frappe.db.escape(course, percent=False) for course in courses
    )
    return f"`tabCourse`.`name` in ({values})"


def course_has_permission(doc, ptype=None, user=None, **kwargs):
    """``has_permission`` hook entry point for Course (True allows).

    A Frappe controller may only deny, never grant: staff and
    administrators keep their role permissions untouched, and an
    instructor keeps read / write / delete on the courses assigned to
    them while every other course is refused. ``create`` stays with the
    role permissions - a document being inserted is not assigned to
    anyone yet, so it cannot be one of the unassigned courses.
    """

    user = user or frappe.session.user

    if not is_instructor_portal(user):
        return True

    if ptype == "create":
        return True

    name = getattr(doc, "name", None)

    # Unsaved desk documents carry a temporary "new-..." name.
    if not name or str(name).startswith("new-"):
        return True

    return name in get_instructor_courses(user)


# =========================================================
# RESTRICT OTHER STUDENTS' RECORDS FOR LEARNERS
#
# These hooks tighten the generic Frappe REST API
# (/api/resource/...) so a learner can only ever read their own
# rows, even though the DocType grants the Student role a read
# permission. Staff and education operators are unaffected.
# =========================================================

RESTRICTED_FOR_STUDENTS = (
    "Student",
    "Program Enrollment",
    "Student Attendance",
    "Student Leave Application",
    "Sales Invoice",
    "Course Schedule",
    "Student Group",
    "Assessment Result",
    "Assessment Plan",
)


def _student_scope_condition(doctype, user):
    """SQL condition limiting a query to the caller's own records."""

    if is_student_portal(user):
        return _learner_scope_condition(doctype, user)

    if is_instructor_portal(user):
        return _instructor_scope_condition(doctype, user)

    return ""


def _learner_scope_condition(doctype, user):

    student = student_name_for_user(user)

    if doctype == "Student":
        if not student:
            return "`tabStudent`.`name` = ''"
        return f"`tabStudent`.`name` = {frappe.db.escape(student, percent=False)}"

    if doctype in (
        "Program Enrollment",
        "Student Attendance",
        "Sales Invoice",
        "Assessment Result",
    ):
        if not student:
            return f"`tab{doctype}`.`student` = ''"
        return f"`tab{doctype}`.`student` = {frappe.db.escape(student, percent=False)}"

    if doctype == "Student Leave Application":
        if not student:
            return f"`tab{doctype}`.`student` = ''"
        return f"`tab{doctype}`.`student` = {frappe.db.escape(student, percent=False)}"

    if doctype in ("Course Schedule", "Student Group"):
        groups = _student_group_names(student)

        if not groups:
            return f"`tab{doctype}`.`name` = ''"

        values = ", ".join(
            frappe.db.escape(group, percent=False) for group in groups
        )
        return f"`tab{doctype}`.`name` in ({values})"

    if doctype == "Assessment Plan":
        # Assessment Plans hang off a Student Group, not a student.
        groups = _student_group_names(student)

        if not groups:
            return "`tabAssessment Plan`.`student_group` = ''"

        values = ", ".join(
            frappe.db.escape(group, percent=False) for group in groups
        )
        return f"`tabAssessment Plan`.`student_group` in ({values})"

    return ""


def _student_group_names(student):
    if not student:
        return []

    return frappe.get_all(
        "Student Group Student",
        filters={"student": student},
        pluck="parent",
    )


def _instructor_scope_condition(doctype, user):
    """SQL condition limiting a query to an instructor's groups."""

    groups = get_instructor_groups(user)
    students = get_instructor_students(user)

    group_values = ", ".join(
        frappe.db.escape(group, percent=False) for group in groups
    )
    student_values = ", ".join(
        frappe.db.escape(student, percent=False) for student in students
    )

    if doctype == "Student":
        if not students:
            return "`tabStudent`.`name` = ''"
        return f"`tabStudent`.`name` in ({student_values})"

    if doctype in (
        "Program Enrollment",
        "Student Attendance",
        "Student Leave Application",
        "Assessment Result",
    ):
        if not students:
            return f"`tab{doctype}`.`student` = ''"
        return f"`tab{doctype}`.`student` in ({student_values})"

    if doctype == "Sales Invoice":
        # Instructors never handle money.
        return "`tabSales Invoice`.`name` = ''"

    if doctype == "Student Group":
        if not groups:
            return "`tabStudent Group`.`name` = ''"
        return f"`tabStudent Group`.`name` in ({group_values})"

    if doctype == "Course Schedule":
        record = get_instructor_record(user)
        if not record:
            return "`tabCourse Schedule`.`name` = ''"
        instructor = frappe.db.escape(record.name, percent=False)
        if not groups:
            return f"`tabCourse Schedule`.`instructor` = {instructor}"
        return (
            f"`tabCourse Schedule`.`instructor` = {instructor} "
            f"or `tabCourse Schedule`.`student_group` in ({group_values})"
        )

    if doctype == "Assessment Plan":
        # Assessment Plans hang off a Student Group, not a student.
        if not groups:
            return "`tabAssessment Plan`.`student_group` = ''"
        return f"`tabAssessment Plan`.`student_group` in ({group_values})"

    return ""


def _student_allowed(doc, user):
    """Document level check: learners / instructors only open their rows."""

    if is_student_portal(user):
        return _learner_allowed(doc, user)

    if is_instructor_portal(user):
        return _instructor_allowed(doc, user)

    return True


def _learner_allowed(doc, user):

    doctype = doc.doctype
    student = student_name_for_user(user)

    if doctype == "Student":
        return bool(student) and doc.name == student

    if doctype in (
        "Program Enrollment",
        "Student Attendance",
        "Student Leave Application",
        "Sales Invoice",
        "Assessment Result",
    ):
        return bool(student) and (doc.student or "") == student

    if doctype in ("Course Schedule", "Student Group"):
        return bool(student) and doc.name in _student_group_names(student)

    if doctype == "Assessment Plan":
        return bool(student) and (
            doc.student_group or ""
        ) in _student_group_names(student)

    return True


def _instructor_allowed(doc, user):

    doctype = doc.doctype

    if doctype == "Sales Invoice":
        return False

    if doctype == "Student":
        return doc.name in get_instructor_students(user)

    if doctype in (
        "Program Enrollment",
        "Student Attendance",
        "Student Leave Application",
        "Assessment Result",
    ):
        return (doc.student or "") in get_instructor_students(user)

    if doctype == "Student Group":
        return doc.name in get_instructor_groups(user)

    if doctype == "Course Schedule":
        record = get_instructor_record(user)
        return bool(record) and (
            doc.instructor == record.name
            or doc.student_group in get_instructor_groups(user)
        )

    if doctype == "Assessment Plan":
        return (doc.student_group or "") in get_instructor_groups(user)

    return True


def student_query_conditions(user, doctype=None):
    """``permission_query_conditions`` hook entry point."""

    return _student_scope_condition(doctype, user)


def student_has_permission(doc, ptype=None, user=None, **kwargs):
    """``has_permission`` hook entry point (returns True to allow)."""

    return _student_allowed(doc, user or frappe.session.user)
