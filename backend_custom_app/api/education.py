import json

import frappe

from frappe.utils import (
    get_first_day,
    get_last_day,
    add_months,
    getdate,
    nowdate
)

from backend_custom_app.api.permissions import (
    get_instructor_groups,
    get_instructor_record,
    get_instructor_students,
    get_student_record,
    is_instructor_portal,
    is_staff,
    is_student_portal,
    require_education_access,
    require_education_scope,
)


# =========================================================
# HELPER FUNCTIONS
# =========================================================

def _existing_fields(doctype, fields):
    """
    Return only fields that actually exist in the DocType.

    This keeps the dashboard APIs safe when optional fields
    differ between Education versions or customizations.
    """
    meta = frappe.get_meta(doctype)

    existing = []

    for field in fields:
        if meta.has_field(field):
            existing.append(field)

    return existing


def _safe_value(value):
    """
    Convert None values into empty strings for frontend use.
    """
    return value if value is not None else ""


# =========================================================
# EDUCATION DASHBOARD
# =========================================================


@frappe.whitelist()
def get_dashboard():

    require_education_scope()

    if is_student_portal():
        return _student_dashboard()

    if is_instructor_portal():
        return _instructor_dashboard()

    return _admin_dashboard()


# =========================================================
# INSTRUCTOR DASHBOARD
#
# Built only from the Student Groups that list this Instructor in
# their ``instructors`` child table - never from the whole school.
# =========================================================


def _instructor_dashboard():

    record = get_instructor_record()

    groups = get_instructor_groups()
    students = get_instructor_students()
    today = nowdate()

    # ---------------------------------------------------------
    # Group cards with student counts
    # ---------------------------------------------------------

    group_cards = []

    if groups:
        for group in frappe.get_all(
            "Student Group",
            filters={"name": ["in", groups]},
            fields=[
                "name",
                "student_group_name",
                "program",
                "course",
                "academic_year",
                "academic_term",
            ],
        ):
            group_cards.append({
                "id": group.name,
                "name": group.student_group_name or group.name,
                "program": group.program or "",
                "course": frappe.db.get_value("Course", group.course, "course_name")
                or group.course
                or "",
                "academicYear": group.academic_year or "",
                "academicTerm": group.academic_term or "",
                "studentCount": frappe.db.count(
                    "Student Group Student",
                    {"parent": group.name, "parenttype": "Student Group"},
                ),
            })

    # ---------------------------------------------------------
    # Today's + upcoming sessions (own sessions only)
    # ---------------------------------------------------------

    def _session_rows(filters, limit):
        rows = frappe.get_all(
            "Course Schedule",
            filters=filters,
            fields=[
                "name",
                "student_group",
                "course",
                "room",
                "schedule_date",
                "from_time",
                "to_time",
            ],
            order_by="schedule_date asc, from_time asc",
            limit_page_length=limit,
        )

        return [
            {
                "id": row.name,
                "studentGroup": row.student_group or "",
                "course": frappe.db.get_value("Course", row.course, "course_name")
                or row.course,
                "room": row.room or "",
                "date": row.schedule_date.isoformat()
                if row.schedule_date
                else "",
                "fromTime": str(row.from_time) if row.from_time else "",
                "toTime": str(row.to_time) if row.to_time else "",
                "isToday": bool(row.schedule_date)
                and str(row.schedule_date) == today,
            }
            for row in rows
        ]

    instructor_name = record.name if record else "__none__"

    today_sessions = _session_rows(
        {"instructor": instructor_name, "schedule_date": today}, 20
    )
    upcoming_sessions = _session_rows(
        {"instructor": instructor_name, "schedule_date": [">", today]}, 20
    )

    # ---------------------------------------------------------
    # Pending leave requests for own students
    # ---------------------------------------------------------

    pending_leaves = []

    if students:
        leave_rows = frappe.get_all(
            "Student Leave Application",
            filters={"student": ["in", students], "docstatus": 0},
            fields=[
                "name",
                "student",
                "student_name",
                "from_date",
                "to_date",
                "reason",
                "docstatus",
            ],
            order_by="creation desc",
            limit_page_length=20,
        )

        pending_leaves = [
            {
                "id": row.name,
                "student": row.student,
                "studentName": row.student_name or "",
                "fromDate": row.from_date.isoformat()
                if row.from_date
                else "",
                "toDate": row.to_date.isoformat() if row.to_date else "",
                "reason": row.reason or "",
                "status": "Pending",
            }
            for row in leave_rows
        ]

    # ---------------------------------------------------------
    # Attendance stats scoped to own students
    # ---------------------------------------------------------

    attendance_stats = {"total": 0, "present": 0, "absent": 0, "leave": 0,
                        "percentage": 0, "students": 0}

    if students:
        attendance_stats = _attendance_stats({"student": ["in", students]})

    return {
        "mode": "instructor",
        "instructor": {
            "id": record.name if record else "",
            "name": (record.instructor_name if record else "") or "",
        },
        "groups": group_cards,
        "students_count": len(students),
        "sessions_count": frappe.db.count(
            "Course Schedule", {"instructor": instructor_name}
        ),
        "today_sessions": today_sessions,
        "upcoming_sessions": upcoming_sessions,
        "pending_leave_requests": pending_leaves,
        "attendance": attendance_stats,
    }


# =========================================================
# STUDENT (LEARNER) DASHBOARD
#
# Only ever built from the Student record linked to the signed-in
# session, so a learner can never see another student or the
# school-wide statistics.
# =========================================================


def _student_dashboard():

    record = get_student_record()

    if not record:
        return {
            "mode": "student",
            "student": None,
            "profile": None,
            "message": (
                "No student record is linked to this account yet. "
                "Contact your administrator."
            ),
        }

    student = record.name

    payload = dict(record)
    for key, value in list(payload.items()):
        if hasattr(value, "isoformat"):
            payload[key] = value.isoformat()

    # ---------------------------------------------------------
    # Academic enrollment
    # ---------------------------------------------------------

    enrollment_rows = frappe.get_all(
        "Program Enrollment",
        filters={"student": student},
        fields=[
            "name",
            "program",
            "academic_year",
            "academic_term",
            "student_batch_name",
            "student_category",
            "enrollment_date",
        ],
        order_by="creation desc",
        limit_page_length=1,
    )

    enrollment = dict(enrollment_rows[0]) if enrollment_rows else {}

    groups = frappe.get_all(
        "Student Group Student",
        filters={"student": student},
        pluck="parent",
    )

    payload.update(
        {
            "program": enrollment.get("program") or "",
            "academic_year": enrollment.get("academic_year") or "",
            "academic_term": enrollment.get("academic_term") or "",
            "batch": enrollment.get("student_batch_name") or "",
            "category": enrollment.get("student_category") or "",
            "groups": groups,
        }
    )

    # ---------------------------------------------------------
    # Profile extras: photo, address, guardians
    #
    # ``get_student_record`` deliberately returns a lean field set
    # (it is cached and reused across every learner API), so the
    # optional profile fields are read here only for this payload.
    # ---------------------------------------------------------

    profile_extra = (
        frappe.db.get_value(
            "Student",
            student,
            [
                "image",
                "address_line_1",
                "address_line_2",
                "city",
                "state",
                "pincode",
                "country",
            ],
            as_dict=True,
        )
        or {}
    )

    image = profile_extra.get("image") or ""

    if not image and record.get("user"):
        image = frappe.db.get_value("User", record.user, "user_image") or ""

    payload["image"] = image
    payload["address_line_1"] = profile_extra.get("address_line_1") or ""
    payload["address_line_2"] = profile_extra.get("address_line_2") or ""
    payload["city"] = profile_extra.get("city") or ""
    payload["state"] = profile_extra.get("state") or ""
    payload["pincode"] = profile_extra.get("pincode") or ""
    payload["country"] = profile_extra.get("country") or ""

    guardians = []

    for row in frappe.get_all(
        "Student Guardian",
        filters={"parent": student, "parenttype": "Student"},
        fields=["guardian", "guardian_name", "relation"],
        order_by="idx asc",
    ):
        guardian_detail = {}

        if row.guardian:
            guardian_detail = (
                frappe.db.get_value(
                    "Guardian",
                    row.guardian,
                    ["guardian_name", "mobile_number", "email_address"],
                    as_dict=True,
                )
                or {}
            )

        guardians.append(
            {
                "name": row.guardian_name
                or guardian_detail.get("guardian_name")
                or "",
                "relation": row.relation or "",
                "mobile": guardian_detail.get("mobile_number") or "",
                "email": guardian_detail.get("email_address") or "",
            }
        )

    payload["guardians"] = guardians

    # ---------------------------------------------------------
    # Courses / classes the learner is enrolled in
    # ---------------------------------------------------------

    courses = []

    if enrollment:
        course_rows = frappe.get_all(
            "Program Enrollment Course",
            filters={
                "parent": enrollment.get("name"),
                "parenttype": "Program Enrollment",
            },
            fields=["course", "course_name"],
        )

        for row in course_rows:
            courses.append(
                row.get("course_name")
                or row.get("course")
                or ""
            )

    if not courses:
        course_rows = frappe.get_all(
            "Student Group",
            filters={"name": ["in", groups]},
            fields=["course"],
        )
        courses = [
            frappe.db.get_value("Course", row.course, "course_name") or row.course
            for row in course_rows
            if row.get("course")
        ]

    courses = [course for course in dict.fromkeys(courses) if course]

    # ---------------------------------------------------------
    # Timetable - own groups only
    # ---------------------------------------------------------

    today = nowdate()

    sessions = []

    if groups:
        session_rows = frappe.get_all(
            "Course Schedule",
            filters={
                "student_group": ["in", groups],
                "schedule_date": [">=", today],
            },
            fields=[
                "name",
                "student_group",
                "course",
                "instructor",
                "room",
                "schedule_date",
                "from_time",
                "to_time",
            ],
            order_by="schedule_date asc, from_time asc",
            limit_page_length=20,
        )

        sessions = [
            {
                "id": row.name,
                "studentGroup": row.student_group,
                "course": frappe.db.get_value("Course", row.course, "course_name")
                or row.course,
                "instructor": frappe.db.get_value(
                    "Instructor", row.instructor, "instructor_name"
                )
                or row.instructor,
                "room": row.room,
                "date": row.schedule_date.isoformat()
                if row.schedule_date
                else "",
                "fromTime": str(row.from_time) if row.from_time else "",
                "toTime": str(row.to_time) if row.to_time else "",
                "isToday": bool(row.schedule_date)
                and str(row.schedule_date) == today,
            }
            for row in session_rows
        ]

    # ---------------------------------------------------------
    # Attendance summary
    # ---------------------------------------------------------

    attendance_rows = frappe.get_all(
        "Student Attendance",
        filters={"student": student, "docstatus": ["!=", 2]},
        fields=["name", "date", "status", "student_group", "course_schedule"],
        order_by="date desc",
        limit_page_length=200,
    )

    present = sum(1 for row in attendance_rows if row.status == "Present")
    absent = sum(1 for row in attendance_rows if row.status == "Absent")
    leave = sum(1 for row in attendance_rows if row.status == "Leave")
    total = len(attendance_rows)

    # ---------------------------------------------------------
    # Fees
    # ---------------------------------------------------------

    fees_payload = _fees_payload(student)

    # ---------------------------------------------------------
    # Recent notifications addressed to this learner
    # ---------------------------------------------------------

    notifications = get_notifications().get("notifications", [])

    # ---------------------------------------------------------
    # Pending leave applications
    # ---------------------------------------------------------

    leave_rows = frappe.get_all(
        "Student Leave Application",
        filters={"student": student},
        fields=["name", "from_date", "to_date", "docstatus", "reason"],
        order_by="creation desc",
        limit_page_length=10,
    )

    leave_applications = [
        {
            "id": row.name,
            "from_date": row.from_date.isoformat() if row.from_date else "",
            "to_date": row.to_date.isoformat() if row.to_date else "",
            "reason": row.reason or "",
            "status": {0: "Pending", 1: "Approved", 2: "Rejected"}.get(
                row.docstatus, "Pending"
            ),
        }
        for row in leave_rows
    ]

    return {
        "mode": "student",
        "student": student,
        "profile": payload,
        "courses": courses,
        "timetable": sessions,
        "attendance": {
            "total": total,
            "present": present,
            "absent": absent,
            "leave": leave,
            "attendance_percentage": round((present / total) * 100, 2)
            if total
            else 0,
            "recent": [
                {
                    "id": row.name,
                    "date": row.date.isoformat() if row.date else "",
                    "status": row.status,
                    "group": row.student_group or "",
                }
                for row in attendance_rows[:10]
            ],
        },
        "fees": fees_payload,
        "notifications": notifications[:5],
        "leave_applications": leave_applications,
    }


def _admin_dashboard():

    # ---------------------------------------------------------
    # Total Students
    # ---------------------------------------------------------

    total_students = frappe.db.count("Student")

    # ---------------------------------------------------------
    # Paid Sales Invoices
    # ---------------------------------------------------------

    paid_invoices = frappe.get_all(
        "Sales Invoice",
        filters={
            "docstatus": 1,
            "status": "Paid"
        },
        fields=[
            "name",
            "grand_total"
        ]
    )

    # ---------------------------------------------------------
    # Outstanding Sales Invoices
    # ---------------------------------------------------------

    outstanding_invoices = frappe.get_all(
        "Sales Invoice",
        filters={
            "docstatus": 1,
            "outstanding_amount": [">", 0]
        },
        fields=[
            "name",
            "outstanding_amount"
        ]
    )

    total_fees_collected = sum(
        invoice.grand_total or 0
        for invoice in paid_invoices
    )

    total_outstanding = sum(
        invoice.outstanding_amount or 0
        for invoice in outstanding_invoices
    )

    # ---------------------------------------------------------
    # Recent Payments
    # ---------------------------------------------------------

    recent_payments = []

    payment_entries = frappe.get_all(
        "Payment Entry",
        filters={
            "docstatus": 1,
            "payment_type": "Receive"
        },
        fields=[
            "name",
            "posting_date",
            "paid_amount",
            "party"
        ],
        order_by="posting_date desc, creation desc",
        limit_page_length=10
    )

    for payment in payment_entries:

        invoice_name = frappe.db.get_value(
            "Payment Entry Reference",
            {
                "parent": payment.name,
                "reference_doctype": "Sales Invoice"
            },
            "reference_name"
        )

        if not invoice_name:
            continue

        student = frappe.db.get_value(
            "Sales Invoice",
            invoice_name,
            "student"
        )

        student_name = None

        if student:
            student_name = frappe.db.get_value(
                "Student",
                student,
                "student_name"
            )

        recent_payments.append({
            "student": student_name or payment.party or "Unknown",
            "invoice": invoice_name,
            "amount": payment.paid_amount or 0,
            "status": "Paid",
            "date": payment.posting_date
        })

    # ---------------------------------------------------------
    # Monthly Fee Collection - Last 6 Months
    # ---------------------------------------------------------

    monthly_fees = []

    current_date = getdate(nowdate())

    for month_offset in range(5, -1, -1):

        month_date = add_months(
            current_date,
            -month_offset
        )

        start_date = get_first_day(month_date)
        end_date = get_last_day(month_date)

        payments = frappe.get_all(
            "Payment Entry",
            filters={
                "docstatus": 1,
                "payment_type": "Receive",
                "posting_date": [
                    "between",
                    [start_date, end_date]
                ]
            },
            fields=[
                "paid_amount"
            ]
        )

        amount = sum(
            payment.paid_amount or 0
            for payment in payments
        )

        monthly_fees.append({
            "month": month_date.strftime("%b"),
            "amount": amount
        })

    # ---------------------------------------------------------
    # Attendance statistics (school wide)
    # ---------------------------------------------------------

    attendance_stats = _attendance_stats()

    # ---------------------------------------------------------
    # Dashboard Response
    # ---------------------------------------------------------

    return {
        "mode": "admin",
        "total_students": total_students,
        "total_fees_collected": total_fees_collected,
        "total_outstanding": total_outstanding,
        "paid_invoices": len(paid_invoices),
        "unpaid_invoices": len(outstanding_invoices),
        "recent_payments": recent_payments,
        "monthly_fees": monthly_fees,
        "attendance": attendance_stats,
    }


def _attendance_stats(filters=None):

    rows = frappe.get_all(
        "Student Attendance",
        filters=dict(filters or {}, **{"docstatus": ["!=", 2]}),
        fields=["student", "status"],
    )

    total = len(rows)
    present = sum(1 for row in rows if row.status == "Present")
    absent = sum(1 for row in rows if row.status == "Absent")
    leave = sum(1 for row in rows if row.status == "Leave")

    return {
        "total": total,
        "present": present,
        "absent": absent,
        "leave": leave,
        "percentage": round((present / total) * 100, 2) if total else 0,
        "students": len({row.student for row in rows}),
    }


# =========================================================
# STUDENTS
# =========================================================


@frappe.whitelist()
def get_students():

    require_education_scope()

    # ---------------------------------------------------------
    # Get Students
    # ---------------------------------------------------------

    # Learners only ever receive their own row; instructors only
    # receive the students of their own groups.
    filters = None

    if is_student_portal():
        record = get_student_record()
        filters = {"name": record.name if record else "__none__"}
    elif is_instructor_portal():
        allowed = get_instructor_students()
        filters = {"name": ["in", allowed or ["__none__"]]}

    # branch / standard / division are custom Student fields; only
    # request them when the site actually has them.
    student_master_fields = _existing_fields(
        "Student",
        [
            "branch",
            "standard",
            "division"
        ]
    )

    students = frappe.get_all(
        "Student",
        filters=filters,
        fields=[
            "name",
            "student_name",
            "student_email_id",
            "enabled"
        ] + student_master_fields,
        order_by="creation desc"
    )

    result = []

    # ---------------------------------------------------------
    # Get Program Information
    # ---------------------------------------------------------

    for student in students:

        enrollment = frappe.get_all(
            "Program Enrollment",
            filters={
                "student": student.name
            },
            fields=[
                "program",
                "academic_year"
            ],
            order_by="creation desc",
            limit_page_length=1
        )

        program = ""
        academic_year = ""

        if enrollment:
            program = enrollment[0].program or ""
            academic_year = enrollment[0].academic_year or ""

        result.append({
            "id": student.name,
            "name": student.student_name,
            "program": program,
            "year": academic_year,
            "branch": _safe_value(student.get("branch")),
            "standard": _safe_value(student.get("standard")),
            "division": _safe_value(student.get("division")),
            "email": student.student_email_id or "",
            "status": "Active" if student.enabled else "Disabled"
        })

    # ---------------------------------------------------------
    # Students Response
    # ---------------------------------------------------------

    return result


# =========================================================
# FEES & PAYMENTS
# =========================================================


@frappe.whitelist()
def get_fees(payload=None):

    require_education_scope()

    if is_instructor_portal():
        frappe.throw(
            "Instructors do not have access to fee records.",
            frappe.PermissionError,
        )

    student = None

    if is_student_portal():
        # Learners only ever receive their own invoices.
        record = get_student_record()

        if not record:
            return {
                "mode": "student",
                "total_collected": 0,
                "total_outstanding": 0,
                "paid_invoices": 0,
                "unpaid_invoices": 0,
                "fees": [],
            }

        student = record.name

    return _fees_payload(student=student, filters=_payload(payload))


def _payload(payload):
    """Accept a dict, a JSON string or nothing at all."""

    if isinstance(payload, str):
        try:
            data = json.loads(payload)
        except ValueError:
            data = {}
    else:
        data = payload or {}

    return data if isinstance(data, dict) else {}


def _fees_payload(student=None, filters=None):
    """Invoice rows (real Sales Invoices only) for admin or learner."""

    filters = filters or {}

    invoice_filters = {
        "docstatus": 1,
        "student": student or ["is", "set"],
    }

    # Staff-only server side filters.
    if not student:
        if filters.get("status") in ("Paid", "Unpaid"):
            if filters["status"] == "Paid":
                invoice_filters["outstanding_amount"] = ["<=", 0]
            else:
                invoice_filters["outstanding_amount"] = [">", 0]

        if filters.get("student"):
            invoice_filters["student"] = filters["student"]

    # ---------------------------------------------------------
    # Get Submitted Sales Invoices
    # ---------------------------------------------------------

    invoices = frappe.get_all(
        "Sales Invoice",
        filters=invoice_filters,
        fields=[
            "name",
            "student",
            "customer",
            "posting_date",
            "due_date",
            "grand_total",
            "outstanding_amount",
            "status",
            "fee_schedule"
        ],
        order_by="posting_date desc, creation desc",
        limit_page_length=500,
    )

    # ---------------------------------------------------------
    # Student master details (branch / standard / division)
    # ---------------------------------------------------------

    student_ids = {invoice.student for invoice in invoices if invoice.student}

    student_details = {}

    if student_ids:
        master_fields = [
            "name",
            "student_name",
        ] + [
            fieldname
            for fieldname in ("branch", "standard", "division")
            if frappe.get_meta("Student").has_field(fieldname)
        ]

        for row in frappe.get_all(
            "Student",
            filters={"name": ["in", list(student_ids)]},
            fields=master_fields,
        ):
            student_details[row.name] = row

    # ---------------------------------------------------------
    # Payments applied to these invoices
    # ---------------------------------------------------------

    payments_by_invoice = {}

    if invoices:
        references = frappe.get_all(
            "Payment Entry Reference",
            filters={
                "reference_doctype": "Sales Invoice",
                "reference_name": ["in", [invoice.name for invoice in invoices]],
                "docstatus": 1,
            },
            fields=["parent", "reference_name", "allocated_amount"],
        )

        entry_dates = {
            row.name: row.posting_date
            for row in frappe.get_all(
                "Payment Entry",
                filters={
                    "name": ["in", [ref.parent for ref in references]],
                    "docstatus": 1,
                },
                fields=["name", "posting_date"],
            )
        }

        for ref in references:
            payments_by_invoice.setdefault(ref.reference_name, []).append(
                {
                    "date": (
                        entry_dates[ref.parent].isoformat()
                        if entry_dates.get(ref.parent)
                        else ""
                    ),
                    "amount": ref.allocated_amount or 0,
                    "entry": ref.parent,
                }
            )

    fees = []

    total_collected = 0
    total_outstanding = 0
    paid_invoices = 0
    unpaid_invoices = 0

    # ---------------------------------------------------------
    # Process Each Invoice
    # ---------------------------------------------------------

    for invoice in invoices:

        # -----------------------------------------------------
        # Student Name
        # -----------------------------------------------------

        details = student_details.get(invoice.student)

        student_name = details.student_name if details else None

        if not student_name:
            student_name = frappe.db.get_value(
                "Student",
                invoice.student,
                "student_name"
            )

        # -----------------------------------------------------
        # Item / Fee Type
        # -----------------------------------------------------

        item_name = frappe.db.get_value(
            "Sales Invoice Item",
            {
                "parent": invoice.name
            },
            "item_name"
        )

        # -----------------------------------------------------
        # Amount Calculation
        # -----------------------------------------------------

        grand_total = invoice.grand_total or 0

        outstanding_amount = (
            invoice.outstanding_amount or 0
        )

        paid_amount = (
            grand_total - outstanding_amount
        )

        # -----------------------------------------------------
        # Payment Status
        # -----------------------------------------------------

        if outstanding_amount <= 0:

            payment_status = "Paid"
            paid_invoices += 1

        else:

            payment_status = "Unpaid"
            unpaid_invoices += 1

        # -----------------------------------------------------
        # Summary Totals
        # -----------------------------------------------------

        total_collected += paid_amount
        total_outstanding += outstanding_amount

        payments = payments_by_invoice.get(invoice.name, [])

        last_payment = max(
            (payment["date"] for payment in payments),
            default="",
        )

        # -----------------------------------------------------
        # Fee Record
        # -----------------------------------------------------

        fees.append({
            "student": student_name or "Unknown",
            "studentId": invoice.student,
            "invoice": invoice.name,
            "feeType": item_name or "Fee",
            "amount": grand_total,
            "paidAmount": paid_amount,
            "outstandingAmount": outstanding_amount,
            "status": payment_status,
            "date": invoice.posting_date,
            "dueDate": invoice.due_date,
            "lastPaymentDate": last_payment,
            "payments": payments,
            "customer": invoice.customer or "",
            "branch": (details.branch if details else "") or "",
            "standard": (details.standard if details else "") or "",
            "division": (details.division if details else "") or "",
            "feeSchedule": invoice.fee_schedule or ""
        })

    # ---------------------------------------------------------
    # Staff only filters applied after the master join
    # ---------------------------------------------------------

    if not student:
        for key in ("branch", "standard", "division"):
            if filters.get(key):
                fees = [row for row in fees if row[key] == filters[key]]

        if fees:
            # Re-run the totals for the filtered set.
            total_collected = sum(row["paidAmount"] for row in fees)
            total_outstanding = sum(row["outstandingAmount"] for row in fees)
            paid_invoices = sum(1 for row in fees if row["status"] == "Paid")
            unpaid_invoices = sum(1 for row in fees if row["status"] == "Unpaid")

    # ---------------------------------------------------------
    # Fees Response
    # ---------------------------------------------------------

    payload = {
        "mode": "student" if student else "admin",
        "total_collected": total_collected,
        "total_outstanding": total_outstanding,
        "paid_invoices": paid_invoices,
        "unpaid_invoices": unpaid_invoices,
        "fees": fees,
    }

    if student:
        payload["student"] = student
        payload["student_name"] = frappe.db.get_value(
            "Student", student, "student_name"
        )

    return payload


# =========================================================
# NOTIFICATIONS
# =========================================================


@frappe.whitelist()
def get_notifications():

    require_education_scope()

    notifications = []

    # ---------------------------------------------------------
    # Get Recent Fee Payment Email Queue Records
    #
    # Only Fee Payment notifications are shown. Emails created
    # for other purposes (welcome mail on Student creation,
    # password reset, etc.) are excluded by matching the email
    # subject line, e.g. "Fee Payment Successful" or
    # "Student Fee Payment Received".
    # ---------------------------------------------------------

    email_queue = frappe.db.sql(
        """
        SELECT
            name,
            status,
            creation,
            reference_doctype,
            reference_name
        FROM `tabEmail Queue`
        WHERE status IN ('Sent', 'Error', 'Not Sent')
            AND SUBSTRING_INDEX(
                SUBSTRING_INDEX(LEFT(message, 1000), 'Subject: ', -1),
                CHAR(10),
                1
            ) LIKE '%Fee Payment%'
        ORDER BY creation DESC
        LIMIT 20
        """,
        as_dict=True,
    )

    # ---------------------------------------------------------
    # Process Email Notifications
    # ---------------------------------------------------------

    for email in email_queue:

        recipients = frappe.get_all(
            "Email Queue Recipient",
            filters={
                "parent": email.name
            },
            fields=[
                "recipient",
                "status",
                "error"
            ]
        )

        for recipient_row in recipients:

            recipient = (
                recipient_row.recipient or ""
            ).strip()

            if not recipient:
                continue

            # -------------------------------------------------
            # Identify Student
            # -------------------------------------------------

            student = frappe.db.get_value(
                "Student",
                {
                    "student_email_id": recipient
                },
                "student_name"
            )

            # -------------------------------------------------
            # Identify Instructor
            # -------------------------------------------------

            instructor = frappe.db.get_value(
                "Instructor",
                {
                    "custom_instructor_email": recipient
                },
                "instructor_name"
            )

            if student:

                recipient_name = student
                role = "Student"

            elif instructor:

                recipient_name = instructor
                role = "Instructor"

            else:

                recipient_name = recipient
                role = "Recipient"

            # -------------------------------------------------
            # Determine Notification Status
            # -------------------------------------------------

            if recipient_row.status == "Sent":

                notification_status = "Sent"

            elif recipient_row.status == "Error":

                notification_status = "Failed"

            elif email.status == "Sent":

                notification_status = "Sent"

            elif email.status == "Error":

                notification_status = "Failed"

            else:

                notification_status = "Pending"

            # -------------------------------------------------
            # Add Notification
            # -------------------------------------------------

            notifications.append({
                "id": f"{email.name}-{recipient}",
                "recipient": recipient_name,
                "contact": recipient,
                "role": role,
                "channel": "Email",
                "purpose": "Fee Payment",
                "status": notification_status,
                "date": email.creation
            })

    # ---------------------------------------------------------
    # Learners only ever see their own notifications
    # ---------------------------------------------------------

    if is_student_portal():

        record = get_student_record()

        own_addresses = set()

        if record:
            own_addresses = {
                (record.student_email_id or "").strip().lower(),
                (record.user or "").strip().lower(),
            }

        notifications = [
            notification
            for notification in notifications
            if (notification.get("contact") or "").strip().lower()
            in own_addresses
        ]

    elif is_instructor_portal():
        # Instructors only see notifications addressed to themselves
        # or to the students of their own groups.
        own_addresses = {(frappe.session.user or "").strip().lower()}

        student_ids = get_instructor_students()
        if student_ids:
            for row in frappe.get_all(
                "Student",
                filters={"name": ["in", student_ids]},
                fields=["student_email_id", "user"],
            ):
                own_addresses.add((row.student_email_id or "").strip().lower())
                own_addresses.add((row.user or "").strip().lower())

        notifications = [
            notification
            for notification in notifications
            if (notification.get("contact") or "").strip().lower()
            in own_addresses
        ]

    # ---------------------------------------------------------
    # WhatsApp Configuration
    # ---------------------------------------------------------

    whatsapp_configured = bool(
        frappe.conf.get("whatsapp_phone_number_id")
        and frappe.conf.get("whatsapp_access_token")
    )

    whatsapp_status = (
        "Configured"
        if whatsapp_configured
        else "Not Configured"
    )

    # ---------------------------------------------------------
    # Summary
    # ---------------------------------------------------------

    total_notifications = len(notifications)

    email_sent = sum(
        1
        for notification in notifications
        if notification["channel"] == "Email"
        and notification["status"] == "Sent"
    )

    email_failed = sum(
        1
        for notification in notifications
        if notification["channel"] == "Email"
        and notification["status"] == "Failed"
    )

    return {
        "total_notifications": total_notifications,
        "email_sent": email_sent,
        "whatsapp_sent": 0,
        "failed": email_failed,
        "whatsapp_status": whatsapp_status,
        "notifications": notifications
    }


# =========================================================
# ATTENDANCE
# =========================================================


@frappe.whitelist()
def get_attendance(payload=None):

    require_education_scope()

    filters = _payload(payload)
    mode = "admin"
    student_filter = None
    instructor_allowed = None

    # ---------------------------------------------------------
    # Learners: own attendance only, whatever was posted
    # ---------------------------------------------------------

    if is_student_portal():
        mode = "student"
        record = get_student_record()
        student_filter = record.name if record else "__none__"

    # ---------------------------------------------------------
    # Instructors: only the students of their own groups
    # ---------------------------------------------------------

    elif is_instructor_portal():
        mode = "instructor"
        instructor_allowed = set(get_instructor_students())

    query_filters = {"docstatus": ["!=", 2]}

    if student_filter:
        query_filters["student"] = student_filter
    elif filters.get("student"):
        if instructor_allowed is not None:
            if filters["student"] not in instructor_allowed:
                frappe.throw(
                    "You can only view attendance for your own students.",
                    frappe.PermissionError,
                )
        query_filters["student"] = filters["student"]

    # ---------------------------------------------------------
    # Staff filters: branch / standard / division / group /
    # date range / status / free text search
    # ---------------------------------------------------------

    if mode in ("admin", "instructor"):

        master_filters = {
            key: filters.get(key)
            for key in ("branch", "standard", "division")
            if filters.get(key)
        }

        # Student scoped filters are combined (AND) before they are
        # handed to the attendance query.
        student_sets = []

        # Instructors may only ever reach their own students.
        if instructor_allowed is not None:
            student_sets.append(set(instructor_allowed))

        if master_filters:
            student_sets.append(
                set(
                    frappe.get_all(
                        "Student",
                        filters=master_filters,
                        pluck="name",
                    )
                )
            )

        if filters.get("search"):
            term = f"%{filters['search'].strip()}%"
            student_sets.append(
                set(
                    frappe.get_all(
                        "Student",
                        or_filters=[
                            ("student_name", "like", term),
                            ("name", "like", term),
                            ("student_email_id", "like", term),
                        ],
                        pluck="name",
                    )
                )
            )

        if student_sets:
            common = set.intersection(*student_sets)
            query_filters["student"] = ["in", sorted(common) or ["__none__"]]

        if filters.get("group"):
            if instructor_allowed is not None:
                if filters["group"] not in get_instructor_groups():
                    frappe.throw(
                        "You can only view attendance for your own groups.",
                        frappe.PermissionError,
                    )
            query_filters["student_group"] = filters["group"]

        if filters.get("status") in ("Present", "Absent", "Leave"):
            query_filters["status"] = filters["status"]

        if filters.get("date"):
            query_filters["date"] = filters["date"]
        elif filters.get("from_date") or filters.get("to_date"):
            between = [
                filters.get("from_date") or "1900-01-01",
                filters.get("to_date") or "2999-12-31",
            ]
            query_filters["date"] = ["between", between]

    # ---------------------------------------------------------
    # Get Attendance Records
    # ---------------------------------------------------------

    attendance_fields = _existing_fields(
        "Student Attendance",
        [
            "name",
            "student",
            "student_name",
            "student_mobile_number",
            "student_group",
            "course_schedule",
            "leave_application",
            "date",
            "status"
        ]
    )

    attendance_records = frappe.get_all(
        "Student Attendance",
        filters=query_filters,
        fields=attendance_fields,
        order_by="date desc, creation desc",
        limit_page_length=1000
    )

    # ---------------------------------------------------------
    # Attendance Summary
    # ---------------------------------------------------------

    total = len(attendance_records)

    present = sum(
        1
        for record in attendance_records
        if record.status == "Present"
    )

    absent = sum(
        1
        for record in attendance_records
        if record.status == "Absent"
    )

    leave = sum(
        1
        for record in attendance_records
        if record.status == "Leave"
    )

    attendance_percentage = (
        (present / total) * 100
        if total
        else 0
    )

    # ---------------------------------------------------------
    # Attendance Records Response
    # ---------------------------------------------------------

    student_ids = {record.student for record in attendance_records if record.student}

    student_masters = {}

    if student_ids:
        master_fields = [
            "name",
            "student_email_id",
        ] + [
            fieldname
            for fieldname in ("branch", "standard", "division")
            if frappe.get_meta("Student").has_field(fieldname)
        ]

        for row in frappe.get_all(
            "Student",
            filters={"name": ["in", list(student_ids)]},
            fields=master_fields,
        ):
            student_masters[row.name] = row

    records = []

    for record in attendance_records:

        course = ""

        if record.course_schedule:

            course = frappe.db.get_value(
                "Course Schedule",
                record.course_schedule,
                "course"
            ) or ""

            course = (
                frappe.db.get_value("Course", course, "course_name") or course
            )

        master = student_masters.get(record.student)

        records.append({
            "id": record.name,
            "studentId": record.student,
            "studentName": record.student_name or "",
            "studentEmail": (master.student_email_id if master else "") or "",
            "studentMobile": record.student_mobile_number or "",
            "studentGroup": record.student_group or "",
            "courseSchedule": record.course_schedule or "",
            "course": course,
            "date": record.date,
            "status": record.status or "",
            "leaveApplication": getattr(record, "leave_application", "") or "",
            "branch": (master.branch if master else "") or "",
            "standard": (master.standard if master else "") or "",
            "division": (master.division if master else "") or ""
        })

    # ---------------------------------------------------------
    # Attendance Response
    # ---------------------------------------------------------

    return {
        "mode": mode,
        "total": total,
        "present": present,
        "absent": absent,
        "leave": leave,
        "attendance_percentage": round(
            attendance_percentage,
            2
        ),
        "records": records
    }


# =========================================================
# COURSES
# =========================================================


@frappe.whitelist()
def get_courses():

    require_education_scope()

    # ---------------------------------------------------------
    # Course Fields
    # ---------------------------------------------------------

    fields = _existing_fields(
        "Course",
        [
            "name",
            "course_name",
            "department",
            "description",
            "default_grading_scale"
        ]
    )

    if "name" not in fields:
        fields.insert(0, "name")

    courses = frappe.get_all(
        "Course",
        fields=fields,
        order_by="creation desc"
    )

    result = []

    for course in courses:

        course_name = (
            course.get("course_name")
            or course.get("name")
            or ""
        )

        department = (
            course.get("department")
            or ""
        )

        description = (
            course.get("description")
            or ""
        )

        result.append({
            "id": course.get("name"),
            "name": course_name,
            "department": department,
            "description": description
        })

    return {
        "total": len(result),
        "courses": result
    }


# =========================================================
# COURSE SCHEDULE / TIMETABLE
# =========================================================


@frappe.whitelist()
def get_course_schedules(payload=None):

    require_education_scope()

    filters = _payload(payload)
    mode = "admin"
    query_filters = {}

    # ---------------------------------------------------------
    # Learners: only the sessions of their own student groups
    # ---------------------------------------------------------

    if is_student_portal():

        mode = "student"
        record = get_student_record()

        groups = (
            frappe.get_all(
                "Student Group Student",
                filters={"student": record.name},
                pluck="parent",
            )
            if record
            else []
        )

        query_filters["student_group"] = ["in", groups or ["__none__"]]

    elif is_instructor_portal():

        # -----------------------------------------------------
        # Instructors: only their own sessions, optionally
        # narrowed by group / course / date filters
        # -----------------------------------------------------

        mode = "instructor"
        record = get_instructor_record()
        my_groups = get_instructor_groups()

        query_filters["instructor"] = record.name if record else "__none__"

        # Group and academic year narrow each other down instead of the
        # last one winning, and both are restricted to the instructor's
        # own groups.
        instructor_group_sets = []

        if filters.get("group"):
            if filters["group"] not in my_groups:
                frappe.throw(
                    "You can only view sessions for your own groups.",
                    frappe.PermissionError,
                )
            instructor_group_sets.append({filters["group"]})

        if filters.get("academic_year"):
            instructor_group_sets.append(
                set(
                    frappe.get_all(
                        "Student Group",
                        filters={
                            "name": ["in", my_groups or ["__none__"]],
                            "academic_year": filters["academic_year"],
                        },
                        pluck="name",
                    )
                )
            )

        if instructor_group_sets:
            common = set.intersection(*instructor_group_sets)
            query_filters["student_group"] = ["in", sorted(common) or ["__none__"]]

        if filters.get("course"):
            query_filters["course"] = filters["course"]

        if filters.get("date"):
            query_filters["schedule_date"] = filters["date"]
        elif filters.get("from_date") or filters.get("to_date"):
            query_filters["schedule_date"] = [
                "between",
                [
                    filters.get("from_date") or "1900-01-01",
                    filters.get("to_date") or "2999-12-31",
                ],
            ]

    else:

        # -----------------------------------------------------
        # Staff filters validated against real relationships
        # -----------------------------------------------------

        group_sets = []

        if filters.get("branch") or filters.get("standard") or filters.get("division"):
            student_filters = {
                key: filters.get(key)
                for key in ("branch", "standard", "division")
                if filters.get(key)
            }
            students = frappe.get_all(
                "Student",
                filters=student_filters,
                pluck="name",
            )
            group_sets.append(
                set(
                    frappe.get_all(
                        "Student Group Student",
                        filters={"student": ["in", students or ["__none__"]]},
                        pluck="parent",
                    )
                )
            )

        if filters.get("academic_year"):
            group_sets.append(
                set(
                    frappe.get_all(
                        "Student Group",
                        filters={"academic_year": filters["academic_year"]},
                        pluck="name",
                    )
                )
            )

        # A picked group is intersected with the other filters instead of
        # replacing them, so "Branch X + Group Y" can no longer return
        # sessions from other branches.
        if filters.get("group"):
            group_sets.append({filters["group"]})

        if group_sets:
            common = set.intersection(*group_sets)
            query_filters["student_group"] = ["in", sorted(common) or ["__none__"]]

        if filters.get("course"):
            query_filters["course"] = filters["course"]

        if filters.get("instructor"):
            query_filters["instructor"] = filters["instructor"]

        if filters.get("date"):
            query_filters["schedule_date"] = filters["date"]
        elif filters.get("from_date") or filters.get("to_date"):
            query_filters["schedule_date"] = [
                "between",
                [
                    filters.get("from_date") or "1900-01-01",
                    filters.get("to_date") or "2999-12-31",
                ],
            ]

    # ---------------------------------------------------------
    # Course Schedule
    # ---------------------------------------------------------

    fields = _existing_fields(
        "Course Schedule",
        [
            "name",
            "student_group",
            "course",
            "instructor",
            "program",
            "room",
            "schedule_date",
            "from_time",
            "to_time",
            "status"
        ]
    )

    if "name" not in fields:
        fields.insert(0, "name")

    schedules = frappe.get_all(
        "Course Schedule",
        filters=query_filters,
        fields=fields,
        order_by="schedule_date desc, from_time asc",
        limit_page_length=500
    )

    # Academic year of each group (used by the timetable filters).
    group_years = {
        row.name: row.academic_year
        for row in frappe.get_all(
            "Student Group",
            filters={"name": ["in", list({s.student_group for s in schedules if s.student_group}) or ["__none__"]]},
            fields=["name", "academic_year"],
        )
    }

    result = []

    for schedule in schedules:

        course_name = (
            schedule.get("course")
            or ""
        )

        instructor_name = (
            schedule.get("instructor")
            or ""
        )

        student_group = (
            schedule.get("student_group")
            or ""
        )

        room = (
            schedule.get("room")
            or ""
        )

        # -----------------------------------------------------
        # Resolve Course Name
        # -----------------------------------------------------

        if course_name:
            resolved_course = frappe.db.get_value(
                "Course",
                course_name,
                "course_name"
            )

            if resolved_course:
                course_name = resolved_course

        # -----------------------------------------------------
        # Resolve Instructor Name
        # -----------------------------------------------------

        if instructor_name:
            resolved_instructor = frappe.db.get_value(
                "Instructor",
                instructor_name,
                "instructor_name"
            )

            if resolved_instructor:
                instructor_name = resolved_instructor

        # -----------------------------------------------------
        # Resolve Student Group Name
        # -----------------------------------------------------

        if student_group:

            group_fields = _existing_fields(
                "Student Group",
                [
                    "student_group_name"
                ]
            )

            if "student_group_name" in group_fields:

                resolved_group = frappe.db.get_value(
                    "Student Group",
                    student_group,
                    "student_group_name"
                )

                if resolved_group:
                    student_group = resolved_group

        result.append({
            "id": schedule.get("name"),
            "studentGroup": student_group,
            "groupId": schedule.get("student_group") or "",
            "course": course_name,
            "courseId": schedule.get("course") or "",
            "instructor": instructor_name,
            "instructorId": schedule.get("instructor") or "",
            "program": schedule.get("program") or "",
            "academicYear": group_years.get(
                schedule.get("student_group")
            )
            or "",
            "room": room,
            "date": schedule.get("schedule_date"),
            "fromTime": schedule.get("from_time"),
            "toTime": schedule.get("to_time"),
            "status": schedule.get("status") or ""
        })

    return {
        "mode": mode,
        "total": len(result),
        "schedules": result
    }


# =========================================================
# ASSESSMENTS
# =========================================================


@frappe.whitelist()
def get_assessments():

    require_education_scope()

    # ---------------------------------------------------------
    # Assessment Plan
    # ---------------------------------------------------------

    fields = _existing_fields(
        "Assessment Plan",
        [
            "name",
            "assessment_name",
            "student_group",
            "course",
            "program",
            "academic_year",
            "academic_term",
            "schedule_date",
            "from_time",
            "to_time",
            "room",
            "examiner",
            "docstatus"
        ]
    )

    if "name" not in fields:
        fields.insert(0, "name")

    # ---------------------------------------------------------
    # Scope: instructors and learners only ever see the plans of
    # the student groups they belong to. Staff see everything.
    # ---------------------------------------------------------

    assessment_filters = {"docstatus": ["!=", 2]}

    if is_instructor_portal():

        assessment_filters["student_group"] = [
            "in",
            get_instructor_groups() or ["__none__"],
        ]

    elif is_student_portal():

        record = get_student_record()

        my_groups = (
            frappe.get_all(
                "Student Group Student",
                filters={"student": record.name},
                pluck="parent",
            )
            if record
            else []
        )

        assessment_filters["student_group"] = ["in", my_groups or ["__none__"]]

    assessments = frappe.get_all(
        "Assessment Plan",
        filters=assessment_filters,
        fields=fields,
        order_by="creation desc",
        limit_page_length=50
    )

    result = []

    for assessment in assessments:

        course_name = (
            assessment.get("course")
            or ""
        )

        if course_name:

            resolved_course = frappe.db.get_value(
                "Course",
                course_name,
                "course_name"
            )

            if resolved_course:
                course_name = resolved_course

        instructor_name = (
            assessment.get("examiner")
            or ""
        )

        if instructor_name:

            resolved_instructor = frappe.db.get_value(
                "Instructor",
                instructor_name,
                "instructor_name"
            )

            if resolved_instructor:
                instructor_name = resolved_instructor

        result.append({
            "id": assessment.get("name"),
            "name": (
                assessment.get("assessment_name")
                or assessment.get("name")
                or ""
            ),
            "studentGroup": (
                assessment.get("student_group")
                or ""
            ),
            "course": course_name,
            "program": (
                assessment.get("program")
                or ""
            ),
            "academicYear": (
                assessment.get("academic_year")
                or ""
            ),
            "academicTerm": (
                assessment.get("academic_term")
                or ""
            ),
            "date": assessment.get("schedule_date"),
            "fromTime": assessment.get("from_time"),
            "toTime": assessment.get("to_time"),
            "room": assessment.get("room") or "",
            "examiner": instructor_name
        })

    return {
        "total": len(result),
        "assessments": result
    }


# =========================================================
# GRADES (learners only)
# =========================================================


@frappe.whitelist()
def get_grades():
    """Assessment results for the signed-in learner only."""

    require_education_access()

    if not is_student_portal():
        frappe.throw(
            "Grades are only available for students.",
            frappe.PermissionError,
        )

    record = get_student_record()

    if not record:
        return {
            "mode": "student",
            "student": None,
            "results": [],
            "courses": [],
            "message": (
                "No student record is linked to this account yet. "
                "Contact your administrator."
            ),
        }

    student = record.name

    # ---------------------------------------------------------
    # Enrolled courses
    # ---------------------------------------------------------

    courses = []

    enrollment = frappe.get_all(
        "Program Enrollment",
        filters={"student": student},
        fields=["name", "program", "academic_year", "academic_term"],
        order_by="creation desc",
        limit_page_length=1,
    )

    if enrollment:
        for row in frappe.get_all(
            "Program Enrollment Course",
            filters={
                "parent": enrollment[0].name,
                "parenttype": "Program Enrollment",
            },
            fields=["course", "course_name"],
        ):
            courses.append(row.course_name or row.course or "")

    courses = [course for course in dict.fromkeys(courses) if course]

    # ---------------------------------------------------------
    # Assessment results (submitted only)
    # ---------------------------------------------------------

    results = []

    rows = frappe.get_all(
        "Assessment Result",
        filters={"student": student, "docstatus": 1},
        fields=[
            "name",
            "assessment_plan",
            "course",
            "program",
            "academic_year",
            "academic_term",
            "student_group",
            "maximum_score",
            "total_score",
            "grade",
            "comment",
        ],
        order_by="creation desc",
        limit_page_length=100,
    )

    for row in rows:
        course_name = frappe.db.get_value("Course", row.course, "course_name") or (
            row.course or ""
        )

        plan_name = ""
        if row.assessment_plan:
            plan_name = (
                frappe.db.get_value(
                    "Assessment Plan", row.assessment_plan, "assessment_name"
                )
                or row.assessment_plan
            )

        details = [
            {
                "criteria": detail.assessment_criteria or "",
                "maximumScore": detail.maximum_score or 0,
                "score": detail.score or 0,
                "grade": detail.grade or "",
            }
            for detail in frappe.get_all(
                "Assessment Result Detail",
                filters={"parent": row.name},
                fields=[
                    "assessment_criteria",
                    "maximum_score",
                    "score",
                    "grade",
                ],
                order_by="idx asc",
            )
        ]

        results.append({
            "id": row.name,
            "assessment": plan_name,
            "course": course_name,
            "program": row.program or "",
            "academicYear": row.academic_year or "",
            "academicTerm": row.academic_term or "",
            "group": row.student_group or "",
            "maximumScore": row.maximum_score or 0,
            "totalScore": row.total_score or 0,
            "grade": row.grade or "",
            "comment": row.comment or "",
            "details": details,
        })

    scores = [row["totalScore"] for row in results if row.get("totalScore")]

    return {
        "mode": "student",
        "student": student,
        "studentName": record.student_name or "",
        "program": enrollment[0].program if enrollment else "",
        "academicYear": enrollment[0].academic_year if enrollment else "",
        "academicTerm": enrollment[0].academic_term if enrollment else "",
        "courses": courses,
        "results": results,
        "summary": {
            "total": len(results),
            "averageScore": round(sum(scores) / len(scores), 2) if scores else 0,
            "latestGrade": results[0]["grade"] if results else "",
        },
    }


# =========================================================
# INSTRUCTORS
# =========================================================


@frappe.whitelist()
def get_instructors():

    require_education_scope()

    # ---------------------------------------------------------
    # Instructor Fields
    # ---------------------------------------------------------

    fields = _existing_fields(
        "Instructor",
        [
            "name",
            "instructor_name",
            "employee",
            "department",
            "status",
            "custom_instructor_email",
            "custom_instructor_mobile_number"
        ]
    )

    if "name" not in fields:
        fields.insert(0, "name")

    instructors = frappe.get_all(
        "Instructor",
        fields=fields,
        order_by="creation desc"
    )

    result = []

    for instructor in instructors:

        result.append({
            "id": instructor.get("name"),
            "name": (
                instructor.get("instructor_name")
                or instructor.get("name")
                or ""
            ),
            "employee": (
                instructor.get("employee")
                or ""
            ),
            "department": (
                instructor.get("department")
                or ""
            ),
            "status": (
                instructor.get("status")
                or ""
            ),
            "email": (
                instructor.get(
                    "custom_instructor_email"
                )
                or ""
            ),
            "mobile": (
                instructor.get(
                    "custom_instructor_mobile_number"
                )
                or ""
            )
        })

    return {
        "total": len(result),
        "instructors": result
    }


# =========================================================
# STUDENT GROUPS
# =========================================================


@frappe.whitelist()
def get_student_groups():

    require_education_scope()

    # ---------------------------------------------------------
    # Student Group Fields
    # ---------------------------------------------------------

    fields = _existing_fields(
        "Student Group",
        [
            "name",
            "student_group_name",
            "group_based_on",
            "program",
            "batch",
            "course",
            "academic_year",
            "academic_term",
            "max_strength"
        ]
    )

    if "name" not in fields:
        fields.insert(0, "name")

    # ---------------------------------------------------------
    # Scope: instructors see the groups they teach, learners the
    # groups they belong to. Staff see every group.
    # ---------------------------------------------------------

    group_filters = {}

    if is_instructor_portal():

        group_filters["name"] = [
            "in",
            get_instructor_groups() or ["__none__"],
        ]

    elif is_student_portal():

        record = get_student_record()

        my_groups = (
            frappe.get_all(
                "Student Group Student",
                filters={"student": record.name},
                pluck="parent",
            )
            if record
            else []
        )

        group_filters["name"] = ["in", my_groups or ["__none__"]]

    groups = frappe.get_all(
        "Student Group",
        filters=group_filters,
        fields=fields,
        order_by="creation desc"
    )

    result = []

    # ---------------------------------------------------------
    # Check Child Tables
    # ---------------------------------------------------------

    student_child_doctype = None

    if frappe.db.exists(
        "DocType",
        "Student Group Student"
    ):
        student_child_doctype = (
            "Student Group Student"
        )

    instructor_child_doctype = None

    if frappe.db.exists(
        "DocType",
        "Student Group Instructor"
    ):
        instructor_child_doctype = (
            "Student Group Instructor"
        )

    # ---------------------------------------------------------
    # Process Groups
    # ---------------------------------------------------------

    for group in groups:

        group_name = (
            group.get("student_group_name")
            or group.get("name")
            or ""
        )

        # -----------------------------------------------------
        # Student Count
        # -----------------------------------------------------

        student_count = 0

        if student_child_doctype:

            student_count = frappe.db.count(
                student_child_doctype,
                {
                    "parent": group.get("name"),
                    "parenttype": "Student Group"
                }
            )

        # -----------------------------------------------------
        # Instructor Count
        # -----------------------------------------------------

        instructor_count = 0

        if instructor_child_doctype:

            instructor_count = frappe.db.count(
                instructor_child_doctype,
                {
                    "parent": group.get("name"),
                    "parenttype": "Student Group"
                }
            )

        # -----------------------------------------------------
        # Group Record
        # -----------------------------------------------------

        result.append({
            "id": group.get("name"),
            "name": group_name,
            "groupBasedOn": (
                group.get("group_based_on")
                or ""
            ),
            "program": (
                group.get("program")
                or ""
            ),
            "batch": (
                group.get("batch")
                or ""
            ),
            "course": (
                group.get("course")
                or ""
            ),
            "academicYear": (
                group.get("academic_year")
                or ""
            ),
            "academicTerm": (
                group.get("academic_term")
                or ""
            ),
            "maxStrength": (
                group.get("max_strength")
                or 0
            ),
            "studentCount": student_count,
            "instructorCount": instructor_count
        })

    return {
        "total": len(result),
        "student_groups": result
    }


# =========================================================
# DAILY ATTENDANCE (staff + owning instructor)
#
# The Education app has no "take attendance for a group" workflow of
# its own (instructors hold no DocPerm on Student Attendance), so the
# portal provides one here. Staff may mark any group; instructors are
# restricted to the groups that list them in ``Student Group Instructor``.
# =========================================================


def _require_group_access(group):
    """Staff may act on any group; instructors only their own groups."""

    if is_staff():
        return

    if is_instructor_portal():
        if group not in get_instructor_groups():
            frappe.throw(
                "You can only take attendance for your own groups.",
                frappe.PermissionError,
            )
        return

    frappe.throw(
        "You do not have permission to mark attendance.",
        frappe.PermissionError,
    )


@frappe.whitelist()
def get_group_attendance_roster(payload=None):
    """Students in a group plus their attendance status for one date."""

    require_education_access()

    data = _payload(payload)
    group = data.get("student_group")
    date = data.get("date") or nowdate()

    if not group or not frappe.db.exists("Student Group", group):
        frappe.throw("Student Group is required.")

    _require_group_access(group)

    students = frappe.get_all(
        "Student Group Student",
        filters={"parent": group, "parenttype": "Student Group"},
        fields=["student", "student_name"],
        order_by="idx asc",
    )

    existing = {
        row.student: row
        for row in frappe.get_all(
            "Student Attendance",
            filters={
                "student_group": group,
                "date": date,
                "docstatus": ("!=", 2),
            },
            fields=["name", "student", "status", "docstatus"],
        )
    }

    roster = []

    for row in students:
        record = existing.get(row.student)
        roster.append({
            "student": row.student,
            "studentName": row.student_name or "",
            "status": (record.status if record else "") or "",
            "attendance": record.name if record else "",
            "submitted": bool(record and record.docstatus == 1),
        })

    return {
        "student_group": group,
        "date": date,
        "students": roster,
        "hasAttendance": bool(existing),
    }


@frappe.whitelist(methods=["POST"])
def mark_attendance(payload=None):
    """Create or update Student Attendance rows for a group on one date.

    An existing row for the same student + group + date is updated in
    place instead of raising the Education duplication error, so re-taking
    attendance simply overwrites the previous marks. New rows are
    inserted and submitted (the Education controller validates group
    membership, duplication and holidays).
    """

    require_education_access()

    data = _payload(payload)
    group = data.get("student_group")
    date = data.get("date") or nowdate()
    rows = data.get("rows") or data.get("records") or []

    if not group or not frappe.db.exists("Student Group", group):
        frappe.throw("Student Group is required.")

    _require_group_access(group)

    if getdate(date) > getdate(nowdate()):
        frappe.throw("Attendance cannot be marked for a future date.")

    if not isinstance(rows, list) or not rows:
        frappe.throw("No attendance rows were provided.")

    members = set(
        frappe.get_all(
            "Student Group Student",
            filters={"parent": group, "parenttype": "Student Group"},
            pluck="student",
        )
    )

    existing = {
        row.student: row
        for row in frappe.get_all(
            "Student Attendance",
            filters={
                "student_group": group,
                "date": date,
                "docstatus": ("!=", 2),
            },
            fields=["name", "student", "status", "docstatus"],
        )
    }

    created = 0
    updated = 0

    for row in rows:
        if not isinstance(row, dict):
            continue

        student = row.get("student")
        status = row.get("status")

        if not student or student not in members:
            frappe.throw(f"{student} is not a member of {group}.")

        if status not in ("Present", "Absent", "Leave"):
            frappe.throw(
                f"Invalid attendance status for {student}: {status}. "
                "Choose Present, Absent or Leave."
            )

        record = existing.get(student)

        if record:
            frappe.db.set_value(
                "Student Attendance",
                record.name,
                "status",
                status,
                update_modified=True,
            )
            updated += 1
        else:
            doc = frappe.new_doc("Student Attendance")
            doc.student = student
            doc.student_name = frappe.db.get_value(
                "Student", student, "student_name"
            )
            doc.student_group = group
            doc.date = date
            doc.status = status
            doc.flags.ignore_permissions = True
            doc.insert()
            doc.submit()
            created += 1

    return {
        "success": True,
        "student_group": group,
        "date": date,
        "created": created,
        "updated": updated,
        "message": (
            f"Attendance saved for {group} on {date} "
            f"({created} new, {updated} updated)."
        ),
    }