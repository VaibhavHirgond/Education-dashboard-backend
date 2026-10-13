"""Session context for the frontend.

Every page asks the backend who the signed-in user is and which
view they are allowed to see. The answer is derived from the
Frappe session and the linked Student record - never from
anything the browser sends.
"""

import frappe

from backend_custom_app.api.permissions import (
    STUDENT_ROLE,
    INSTRUCTOR_ROLE,
    get_instructor_groups,
    get_instructor_record,
    get_student_record,
    is_instructor_portal,
    is_staff,
    is_student_portal,
    require_education_access,
)


@frappe.whitelist()
def get_context():
    """Return the caller's role context for the Vue application."""

    require_education_access()

    user = frappe.session.user
    roles = frappe.get_roles(user)
    staff = is_staff(user)
    instructor_portal = is_instructor_portal(user)
    student_portal = is_student_portal(user)
    student = get_student_record(user) if student_portal else None
    instructor = get_instructor_record(user) if instructor_portal else None

    visible_roles = [
        role
        for role in roles
        if role in (
            STUDENT_ROLE,
            INSTRUCTOR_ROLE,
            "Education User",
            "Education Manager",
            "Academics User",
            "Instructor",
            "Guardian",
            "System Manager",
            "Administrator",
        )
    ]

    payload = {
        "user": user,
        "full_name": frappe.utils.get_fullname(user) or user,
        "roles": visible_roles,
        "is_staff": staff,
        "is_instructor": instructor_portal,
        "is_student": student_portal,
        "has_student_record": bool(student),
        "student": _serialize(student),
    }

    if instructor_portal:
        payload["instructor"] = {
            "id": instructor.name,
            "name": instructor.instructor_name or instructor.name,
            "groups": get_instructor_groups(user),
        }
    else:
        payload["instructor"] = None

    return payload


def _serialize(student):
    """Student row as a plain dict for the frontend."""

    if not student:
        return None

    payload = dict(student)

    for key, value in list(payload.items()):
        if hasattr(value, "isoformat"):
            payload[key] = value.isoformat()

    enrollment = frappe.get_all(
        "Program Enrollment",
        filters={"student": student.name},
        fields=[
            "program",
            "academic_year",
            "academic_term",
            "student_batch_name",
            "student_category",
        ],
        order_by="creation desc",
        limit_page_length=1,
    )

    if enrollment:
        payload.update(dict(enrollment[0]))

    payload["groups"] = frappe.get_all(
        "Student Group Student",
        filters={"student": student.name},
        pluck="parent",
    )

    return payload
