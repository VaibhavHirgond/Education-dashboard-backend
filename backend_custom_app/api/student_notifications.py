"""Student-scoped in-app notifications backed by Frappe Notification Log."""

import frappe
from frappe.utils import add_days, flt, formatdate, strip_html, today

from backend_custom_app.api.permissions import (
    get_student_record,
    is_student_portal,
    require_education_access,
)

NOTIFICATION_TYPE = "Alert"
PORTAL_ROUTES = {
    "Assessment Result": "/grades",
    "Course Schedule": "/timetable",
    "Payment Entry": "/fees",
    "Sales Invoice": "/fees",
    "Student Attendance": "/attendance",
    "Student Leave Application": "/attendance",
}
CATEGORIES = {
    "Assessment Result": "Grades",
    "Assessment Plan": "Assessment",
    "Course Schedule": "Schedule",
    "Payment Entry": "Fees",
    "Sales Invoice": "Fees",
    "Student Attendance": "Attendance",
    "Student Leave Application": "Leave",
}


def _current_student():
    """Require a student-role session with a validated Student mapping."""

    require_education_access()

    if not is_student_portal():
        frappe.throw("Student notifications are only available to student accounts.", frappe.PermissionError)

    student = get_student_record()
    if not student:
        frappe.throw("No student record is linked to this account.", frappe.PermissionError)

    return student


def _student_user(student_name):
    """Resolve an enabled Frappe user from a Student link or email mapping."""

    student = frappe.db.get_value(
        "Student",
        student_name,
        ["user", "student_email_id", "enabled"],
        as_dict=True,
    )
    if not student or not student.enabled:
        return None

    user = student.user or student.student_email_id
    if not user or not frappe.db.exists("User", {"name": user, "enabled": 1}):
        return None

    return user


def create_student_notification(
    student,
    title,
    description,
    document_type,
    document_name,
):
    """Create an idempotent in-app notification for one linked student."""

    user = _student_user(student)
    if not user or not title:
        return None

    dedupe_filters = {
        "for_user": user,
        "type": NOTIFICATION_TYPE,
        "document_type": document_type,
        "document_name": document_name,
        "title": title,
    }
    if frappe.db.exists("Notification Log", dedupe_filters):
        return None

    route = PORTAL_ROUTES.get(document_type)
    notification = frappe.get_doc(
        {
            "doctype": "Notification Log",
            "type": NOTIFICATION_TYPE,
            "title": title,
            "description": description or "",
            "for_user": user,
            "document_type": document_type,
            "document_name": document_name,
            "app": "education",
            "link": f"/student-portal/#{route}" if route else "",
        }
    )
    notification.insert(ignore_permissions=True)
    return notification.name


def _category(document_type):
    return CATEGORIES.get(document_type, "Education")


def _notification_filters(user):
    return {"for_user": user, "app": "education"}


def _serialize_notification(row):
    document_type = row.get("document_type") or ""
    creation = row.get("creation")

    return {
        "name": row.name,
        "title": row.title or "Education update",
        "description": strip_html(row.description or ""),
        "type": row.type or NOTIFICATION_TYPE,
        "category": _category(document_type),
        "creation": creation.isoformat() if hasattr(creation, "isoformat") else str(creation or ""),
        "read": bool(row.read),
        "document_type": document_type,
        "document_name": row.document_name or "",
        "destination": PORTAL_ROUTES.get(document_type, ""),
    }


@frappe.whitelist(methods=["GET"])
def get_student_notifications(limit=100):
    """List only Education Notification Logs addressed to the current student."""

    _current_student()
    user = frappe.session.user
    filters = _notification_filters(user)

    try:
        page_length = max(1, min(int(limit), 100))
    except (TypeError, ValueError):
        page_length = 100

    rows = frappe.get_all(
        "Notification Log",
        filters=filters,
        fields=[
            "name",
            "title",
            "description",
            "type",
            "creation",
            "read",
            "document_type",
            "document_name",
        ],
        order_by="creation desc",
        limit_page_length=page_length,
    )

    return {
        "notifications": [_serialize_notification(row) for row in rows],
        "unread_count": frappe.db.count("Notification Log", {**filters, "read": 0}),
    }


@frappe.whitelist(methods=["POST"])
def mark_student_notification_read(notification_name=None):
    """Mark one of the current student's Education notifications as read."""

    _current_student()
    if not notification_name:
        frappe.throw("Notification is required.")

    filters = {
        **_notification_filters(frappe.session.user),
        "name": notification_name,
    }
    if not frappe.db.exists("Notification Log", filters):
        frappe.throw("Notification not found.", frappe.PermissionError)

    frappe.db.set_value(
        "Notification Log",
        filters,
        "read",
        1,
        update_modified=False,
    )
    return {"success": True}


@frappe.whitelist(methods=["POST"])
def mark_all_student_notifications_read():
    """Mark all Education notifications for the current student as read."""

    _current_student()
    filters = _notification_filters(frappe.session.user)
    frappe.db.set_value(
        "Notification Log",
        {**filters, "read": 0},
        "read",
        1,
        update_modified=False,
    )
    return {"success": True}


def attendance_submitted(doc, method=None):
    if doc.docstatus != 1:
        return

    course = frappe.db.get_value("Course Schedule", doc.course_schedule, "course") if doc.course_schedule else ""
    status = doc.status or "Attendance"
    date = doc.date or "your scheduled date"
    details = f"Attendance for {date} is marked {status}."
    if course:
        details += f" Course: {course}."

    create_student_notification(
        doc.student,
        f"Attendance marked: {status}",
        details,
        "Student Attendance",
        doc.name,
    )


def attendance_updated(doc, method=None):
    previous = doc.get_doc_before_save()
    if doc.docstatus != 1 or not previous or previous.status == doc.status:
        return

    attendance_submitted(doc, method)


def leave_approved(doc, method=None):
    if doc.docstatus != 1:
        return

    create_student_notification(
        doc.student,
        "Leave application approved",
        f"Your leave application from {doc.from_date} to {doc.to_date} was approved.",
        "Student Leave Application",
        doc.name,
    )


def leave_rejected(doc, method=None):
    create_student_notification(
        doc.student,
        "Leave application rejected",
        f"Your leave application from {doc.from_date} to {doc.to_date} was rejected.",
        "Student Leave Application",
        doc.name,
    )


def assessment_result_submitted(doc, method=None):
    if doc.docstatus != 1:
        return

    course = doc.course or "your assessment"
    result = f"Grade: {doc.grade or 'Not graded'}. Score: {doc.total_score or 0} / {doc.maximum_score or 0}."
    if doc.comment:
        result += f" Teacher comment: {doc.comment}"

    create_student_notification(
        doc.student,
        f"New grade available: {course}",
        result,
        "Assessment Result",
        doc.name,
    )


# =========================================================
# ASSESSMENT PLAN -> STUDENT NOTIFICATIONS
# =========================================================


def assessment_plan_submitted(doc, method=None):
    """Notify the plan's student group once, when the plan is submitted.

    Assessment Plan is submittable, so ``on_submit`` fires exactly once
    per document lifecycle (draft edits and ordinary saves never reach
    it). Each recipient is additionally guarded by an existing
    ``Notification Log`` check, so a re-run, retry or re-hook never
    duplicates a delivered notification. Every per-student failure is
    logged and swallowed so a broken mail server or a missing student
    user can never roll back a valid Assessment Plan.
    """

    if doc.docstatus != 1:
        return

    students = _plan_students(doc.student_group)

    if not students:
        frappe.log_error(
            title=f"Assessment Plan notification: empty group ({doc.name})",
            message=(
                f"Assessment Plan {doc.name} was submitted but student group "
                f"'{doc.student_group}' has no active students, so nobody was notified."
            ),
        )
        return

    subject = _assessment_plan_subject(doc)
    message = _assessment_plan_message(doc)

    for student in students:
        try:
            user = _student_user(student)

            if not user:
                frappe.log_error(
                    title=f"Assessment Plan notification: no user ({doc.name})",
                    message=(
                        f"Student '{student}' in group '{doc.student_group}' has no "
                        f"linked enabled user; the notification for Assessment Plan "
                        f"{doc.name} was skipped for them."
                    ),
                )
                continue

            # Idempotency: one in-app notification (and therefore one
            # email) per student per plan, no matter how often the hook
            # or a background retry runs.
            if _already_notified(user, doc.name):
                continue

            create_student_notification(
                student,
                subject,
                message,
                "Assessment Plan",
                doc.name,
            )
            _send_assessment_plan_email(user, subject, message)
        except Exception:
            # Never re-raise: the plan is already submitted and valid.
            frappe.log_error(
                title=f"Assessment Plan notification failed ({doc.name})",
                message=frappe.get_traceback(),
            )


def _assessment_plan_subject(doc):
    label = doc.assessment_name or doc.assessment_group or doc.name
    return f"New Assessment Scheduled: {label}"


def _plan_students(student_group):
    """Active students of the plan's group, as Student names."""

    return frappe.get_all(
        "Student Group Student",
        filters={"parent": student_group, "active": 1},
        pluck="student",
    )


def _student_group_label(group):
    return frappe.db.get_value("Student Group", group, "student_group_name") or group


def _already_notified(user, document_name):
    return bool(
        frappe.db.exists(
            "Notification Log",
            {
                "for_user": user,
                "type": NOTIFICATION_TYPE,
                "document_type": "Assessment Plan",
                "document_name": document_name,
            },
        )
    )


def _assessment_plan_message(doc):
    """Plain-text summary built from the fields actually on the plan.

    The Assessment Plan DocType has no validity / end-date field, so no
    fabricated date is ever shown - only the real schedule information.
    """

    group_label = _student_group_label(doc.student_group)

    lines = [
        "A new assessment has been scheduled for your student group.",
        "",
        f"- Assessment: {doc.assessment_name or doc.assessment_group or doc.name}",
    ]

    if doc.course:
        lines.append(f"- Course: {doc.course}")

    if group_label:
        lines.append(f"- Student Group: {group_label}")

    if doc.assessment_group:
        lines.append(f"- Assessment Group: {doc.assessment_group}")

    if doc.schedule_date:
        lines.append(f"- Scheduled Date: {formatdate(doc.schedule_date)}")

    if doc.from_time and doc.to_time:
        lines.append(f"- Time: {doc.from_time} to {doc.to_time}")

    if doc.room:
        lines.append(f"- Room: {doc.room}")

    if doc.maximum_assessment_score:
        lines.append(f"- Maximum Score: {flt(doc.maximum_assessment_score)}")

    if doc.academic_year:
        lines.append(f"- Academic Year: {doc.academic_year}")

    if doc.academic_term:
        lines.append(f"- Academic Term: {doc.academic_term}")

    if doc.examiner:
        lines.append(f"- Examiner: {doc.examiner_name or doc.examiner}")

    return "\n".join(lines)


def _send_assessment_plan_email(user, subject, message):
    """Queue one email for a student user; failures propagate to the
    caller's per-student try/except (logged, never fatal)."""

    if not user or user in ("Guest", "Administrator"):
        return

    frappe.sendmail(
        recipients=[user],
        subject=subject,
        message=message.replace("\n", "<br>"),
    )


def payment_entry_submitted(doc, invoice):
    """Notify only for a submitted receipt against a submitted invoice."""

    if doc.docstatus != 1 or invoice.docstatus != 1 or not invoice.student:
        return

    amount = sum(
        flt(reference.allocated_amount)
        for reference in doc.references
        if reference.reference_doctype == "Sales Invoice"
        and reference.reference_name == invoice.name
    )
    if amount <= 0:
        return

    currency = invoice.currency or doc.paid_from_account_currency or ""
    create_student_notification(
        invoice.student,
        "Fee payment received",
        f"A payment of {currency} {amount} was recorded for invoice {invoice.name}.",
        "Payment Entry",
        doc.name,
    )


def notify_upcoming_classes():
    """Create one in-app reminder for each student's class scheduled tomorrow."""

    schedule_date = add_days(today(), 1)
    schedules = frappe.get_all(
        "Course Schedule",
        filters={"schedule_date": schedule_date},
        fields=["name", "student_group", "course", "room", "from_time", "to_time"],
        limit_page_length=1000,
    )

    for schedule in schedules:
        students = frappe.get_all(
            "Student Group Student",
            filters={"parent": schedule.student_group, "active": 1},
            pluck="student",
        )
        course_name = frappe.db.get_value("Course", schedule.course, "course_name") or schedule.course
        group_name = (
            frappe.db.get_value("Student Group", schedule.student_group, "student_group_name")
            or schedule.student_group
        )
        time = f"{schedule.from_time or ''} - {schedule.to_time or ''}".strip(" -")
        description = f"{course_name} for {group_name} is scheduled on {schedule_date}."
        if time:
            description += f" Time: {time}."
        if schedule.room:
            description += f" Room: {schedule.room}."

        for student in students:
            create_student_notification(
                student,
                "Upcoming class tomorrow",
                description,
                "Course Schedule",
                schedule.name,
            )
