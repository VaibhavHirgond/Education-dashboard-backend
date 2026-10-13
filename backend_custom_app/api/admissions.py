"""Admin Admissions: Student Applicant + Student Admission management.

Server-side API for the Vue Admissions section. Every endpoint is
staff-only (``require_staff``), so Students and Instructors are rejected
even if they call the API directly. The installed Education workflow is
preserved verbatim:

* ``application_status`` is read-only in the schema and is only ever
  changed by the Approve / Reject / Enroll actions - never by the general
  save endpoint.
* Approve:  Applied -> Approved, Rejected -> Approved.
* Reject:   Applied -> Rejected, Approved -> Rejected.
* Enroll:   Approved -> creates the Student + Program Enrollment (the
  same mapped-doc flow the Education controller uses) -> Admitted.

No field, status, or workflow is invented - only the installed Education
DocTypes, fields, validations, and naming series are used.
"""

import json
from contextlib import contextmanager

import frappe
from frappe.model.mapper import get_mapped_doc
from frappe.utils import getdate, today

from backend_custom_app.api.permissions import require_staff
from backend_custom_app.api.students import (
    _ensure_portal_user,
    _elevated_for_customer_write,
    _precreate_portal_user,
)

# Scalar fields accepted on create/update. ``application_status``,
# ``name``, ``title`` are handled separately (workflow / server-owned).
# ``naming_series`` and ``paid`` are validated explicitly below.
SCALAR_FIELDS = (
    "first_name",
    "middle_name",
    "last_name",
    "application_date",
    "program",
    "student_email_id",
    "student_admission",
    "student_category",
    "academic_year",
    "academic_term",
    "date_of_birth",
    "gender",
    "blood_group",
    "student_mobile_number",
    "nationality",
    "address_line_1",
    "address_line_2",
    "city",
    "state",
    "pincode",
    "country",
)

# Link doctypes the frontend may request options for. Anything else is
# rejected so this endpoint can never be used to enumerate arbitrary
# DocTypes.
LINK_OPTION_DOCTYPES = {
    "Program": "program_name",
    "Academic Year": "academic_year_name",
    "Academic Term": "term_name",
    "Student Category": "category",
    "Student Admission": "title",
    "Gender": "name",
    "Country": "name",
    "Guardian": "guardian_name",
    "Student": "student_name",
}

# Guardian child-table fields the form may submit.
GUARDIAN_FIELDS = ("guardian", "relation")

# Sibling child-table fields the form may submit.
SIBLING_FIELDS = (
    "studying_in_same_institute",
    "full_name",
    "gender",
    "student",
    "institution",
    "program",
    "date_of_birth",
)

# Statuses an applicant may be moved to, and from which.
VALID_STATUS = ("Applied", "Approved", "Rejected", "Admitted")


def _payload(payload):
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except ValueError:
            frappe.throw("Invalid request payload.")
    return payload or {}


def _clean(data, key):
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _serialize(doc):
    """Applicant document as a plain dict, including child tables."""

    if not doc:
        return None

    data = doc.as_dict(no_nulls=False)

    for key, value in list(data.items()):
        if hasattr(value, "isoformat"):
            data[key] = value.isoformat()

    # Child tables -> list of dicts (dates ISO-formatted).
    for table_field in ("guardians", "siblings"):
        rows = []
        for row in doc.get(table_field) or []:
            row_data = row.as_dict(no_nulls=False)
            for key, value in list(row_data.items()):
                if hasattr(value, "isoformat"):
                    row_data[key] = value.isoformat()
            rows.append(row_data)
        data[table_field] = rows

    return data


def _link_options(doctype, filters=None):
    """Return [{name, label}] for a whitelisted Link doctype."""

    meta = frappe.get_meta(doctype)
    title_field = meta.title_field if getattr(meta, "title_field", None) else "name"

    fields = ["name"]
    if title_field != "name":
        fields.append(title_field)

    rows = frappe.get_all(
        doctype,
        filters=filters or {},
        fields=fields,
        order_by="name asc",
        limit_page_length=500,
    )

    seen = set()
    options = []
    for row in rows:
        name = row.get("name")
        if name in seen:
            continue
        seen.add(name)
        options.append({"name": name, "label": row.get(title_field) or name})

    return options


# =========================================================
# LIST / READ
# =========================================================


@frappe.whitelist()
def get_applicants(
    payload=None,
    search=None,
    application_status=None,
    program=None,
    academic_year=None,
    academic_term=None,
    paid=None,
    limit_start=None,
    limit_page_length=None,
):
    """List Student Applicants with search, filters and pagination.

    Accepts either a single JSON ``payload`` object (legacy/POST) or the
    individual query parameters the frontend ``apiGet`` sends. Individual
    params win when both are present.
    """

    require_staff()

    data = _payload(payload)
    for key, value in (
        ("search", search),
        ("application_status", application_status),
        ("program", program),
        ("academic_year", academic_year),
        ("academic_term", academic_term),
        ("paid", paid),
        ("limit_start", limit_start),
        ("limit_page_length", limit_page_length),
    ):
        if value is not None:
            data[key] = value

    filters = {}
    or_filters = None

    status = data.get("application_status")
    if status in VALID_STATUS:
        filters["application_status"] = status

    program = _clean(data, "program")
    if program:
        filters["program"] = program

    academic_year = _clean(data, "academic_year")
    if academic_year:
        filters["academic_year"] = academic_year

    academic_term = _clean(data, "academic_term")
    if academic_term:
        filters["academic_term"] = academic_term

    paid = data.get("paid")
    if paid in (0, 1, "0", "1", True, False):
        filters["paid"] = 1 if paid in (1, "1", True) else 0

    search = _clean(data, "search")
    if search:
        term = f"%{search}%"
        or_filters = [
            ("name", "like", term),
            ("first_name", "like", term),
            ("middle_name", "like", term),
            ("last_name", "like", term),
            ("student_email_id", "like", term),
            ("title", "like", term),
        ]

    try:
        limit_page_length = int(data.get("limit_page_length") or 20)
    except (TypeError, ValueError):
        limit_page_length = 20
    limit_page_length = max(1, min(limit_page_length, 100))

    try:
        limit_start = int(data.get("limit_start") or 0)
    except (TypeError, ValueError):
        limit_start = 0
    limit_start = max(0, limit_start)

    if or_filters:
        total = len(
            frappe.get_all(
                "Student Applicant",
                filters=filters,
                or_filters=or_filters,
                pluck="name",
                limit_page_length=0,
            )
        )
    else:
        total = frappe.db.count("Student Applicant", filters=filters)

    rows = frappe.get_all(
        "Student Applicant",
        filters=filters,
        or_filters=or_filters,
        fields=[
            "name",
            "title",
            "first_name",
            "middle_name",
            "last_name",
            "application_status",
            "application_date",
            "program",
            "academic_year",
            "academic_term",
            "student_email_id",
            "student_admission",
            "student_category",
            "paid",
            "modified",
        ],
        order_by="modified desc",
        limit_start=limit_start,
        limit_page_length=limit_page_length,
    )

    return {
        "total": total,
        "limit_start": limit_start,
        "limit_page_length": limit_page_length,
        "applicants": rows,
    }


@frappe.whitelist()
def get_applicant(name=None):
    """Fetch a single Student Applicant (with child tables) for editing."""

    require_staff()

    if not name or not frappe.db.exists("Student Applicant", name):
        frappe.throw("Student Applicant not found.")

    doc = frappe.get_doc("Student Applicant", name)
    return _serialize(doc)


@frappe.whitelist()
def get_admissions(payload=None, academic_year=None):
    """List Student Admission records (the admission cycles).

    Accepts either a JSON ``payload`` or the individual query parameter the
    frontend sends.
    """

    require_staff()

    data = _payload(payload)
    if academic_year is not None:
        data["academic_year"] = academic_year

    academic_year = _clean(data, "academic_year")
    filters = {}
    if academic_year:
        filters["academic_year"] = academic_year

    rows = frappe.get_all(
        "Student Admission",
        filters=filters,
        fields=[
            "name",
            "title",
            "academic_year",
            "admission_start_date",
            "admission_end_date",
            "published",
            "enable_admission_application",
            "modified",
        ],
        order_by="modified desc",
        limit_page_length=200,
    )

    for row in rows:
        row["program_count"] = frappe.db.count(
            "Student Admission Program", {"parent": row.name}
        )

    return {"total": len(rows), "admissions": rows}


@frappe.whitelist()
def get_admission(name=None):
    """Fetch a single Student Admission with its program_details rows."""

    require_staff()

    if not name or not frappe.db.exists("Student Admission", name):
        frappe.throw("Student Admission not found.")

    doc = frappe.get_doc("Student Admission", name)
    return _serialize_admission(doc)


@frappe.whitelist()
def get_link_options(doctype=None, filters=None):
    """Return options for a whitelisted Link field's target DocType.

    ``filters`` may arrive as a dict or as a JSON string (the frontend
    serializes objects for query-string GETs).
    """

    require_staff()

    if doctype not in LINK_OPTION_DOCTYPES:
        frappe.throw("This field is not supported.")

    if isinstance(filters, str):
        filters = filters.strip()
        if filters:
            try:
                filters = json.loads(filters)
            except ValueError:
                frappe.throw("Invalid filters.")
        else:
            filters = None
    if filters and not isinstance(filters, dict):
        frappe.throw("Invalid filters.")

    return {"doctype": doctype, "options": _link_options(doctype, filters)}


# =========================================================
# STUDENT ADMISSION (admission cycle) CRUD
# =========================================================


# Scalar fields a client may set on a Student Admission.
ADMISSION_SCALAR_FIELDS = (
    "title",
    "route",
    "published",
    "enable_admission_application",
    "academic_year",
    "admission_start_date",
    "admission_end_date",
    "introduction",
)

# Child-table (Student Admission Program) fields the form may submit.
ADMISSION_PROGRAM_FIELDS = (
    "program",
    "min_age",
    "max_age",
    "description",
    "application_fee",
    "applicant_naming_series",
)


def _serialize_admission(doc):
    """Student Admission document as a plain dict, with child rows."""

    if not doc:
        return None

    data = doc.as_dict(no_nulls=False)

    for key, value in list(data.items()):
        if hasattr(value, "isoformat"):
            data[key] = value.isoformat()

    rows = []
    for row in doc.get("program_details") or []:
        row_data = row.as_dict(no_nulls=False)
        for key, value in list(row_data.items()):
            if hasattr(value, "isoformat"):
                row_data[key] = value.isoformat()
        rows.append(row_data)
    data["program_details"] = rows

    return data


def _set_admission_programs(doc, data):
    """Replace program_details rows from the submitted payload."""

    if "program_details" not in data:
        return

    doc.set("program_details", [])
    for row in data.get("program_details") or []:
        if not isinstance(row, dict):
            continue
        program = _clean(row, "program")
        if not program:
            continue
        if not frappe.db.exists("Program", program):
            frappe.throw(
                f"Program '{program}' does not exist.",
                title="Invalid Program",
            )
        entry = {"program": program}
        for fieldname in ADMISSION_PROGRAM_FIELDS:
            if fieldname == "program":
                continue
            value = _clean(row, fieldname)
            if value is not None:
                entry[fieldname] = value
        doc.append("program_details", entry)


@frappe.whitelist(methods=["POST"])
def save_admission(payload=None):
    """Create or update a Student Admission (staff only).

    The ``program_details`` child table (Student Admission Program) is
    fully replaced from the payload, exactly like Desk's child-table
    editor. Only installed fields are written.
    """

    require_staff()

    data = _payload(payload)
    if not isinstance(data, dict):
        frappe.throw("Invalid request payload.")

    name = _clean(data, "name")
    creating = not name

    if not _clean(data, "academic_year"):
        frappe.throw("Academic Year is required.", title="Missing field")

    title = _clean(data, "title")
    if creating and not title:
        frappe.throw("Title is required.", title="Missing field")

    # Date window sanity (mirror Desk's date validation).
    start = _clean(data, "admission_start_date")
    end = _clean(data, "admission_end_date")
    if start and end and getdate(start) > getdate(end):
        frappe.throw(
            "Admission End Date cannot be before Admission Start Date.",
            title="Invalid date window",
        )

    if creating:
        doc = frappe.new_doc("Student Admission")
    else:
        if not frappe.db.exists("Student Admission", name):
            frappe.throw("Student Admission not found.")
        doc = frappe.get_doc("Student Admission", name)

    # Scalar fields.
    meta = frappe.get_meta("Student Admission")
    for fieldname in ADMISSION_SCALAR_FIELDS:
        if not meta.has_field(fieldname):
            continue
        if fieldname in data:
            value = _clean(data, fieldname)
            if fieldname in ("published", "enable_admission_application"):
                doc.set(fieldname, 1 if value in (1, "1", True) else 0)
            elif value is not None:
                doc.set(fieldname, value)
            else:
                doc.set(fieldname, None)

    _set_admission_programs(doc, data)

    doc.flags.ignore_permissions = True
    doc.save()

    return {
        "success": True,
        "name": doc.name,
        "message": f"Student Admission {doc.name} was saved.",
    }


@frappe.whitelist(methods=["POST"])
def delete_admission(name=None):
    """Delete a Student Admission (staff only)."""

    require_staff()

    if not name or not frappe.db.exists("Student Admission", name):
        frappe.throw("Student Admission not found.")

    frappe.delete_doc("Student Admission", name, ignore_permissions=True)

    return {
        "success": True,
        "name": name,
        "message": f"Student Admission {name} was deleted.",
    }


# =========================================================
# CREATE / UPDATE
# =========================================================


def _validate_required(data, creating):
    """Enforce the installed required fields before touching the doc."""

    if not creating:
        return

    if not _clean(data, "first_name"):
        frappe.throw("First Name is required.", title="Missing field")

    if not _clean(data, "program"):
        frappe.throw("Program is required.", title="Missing field")

    if not _clean(data, "academic_year"):
        frappe.throw("Academic Year is required.", title="Missing field")

    email = _clean(data, "student_email_id")
    if email and ("@" not in email or "." not in email.split("@")[-1]):
        frappe.throw(
            "Please enter a valid Student Email Address.", title="Invalid email"
        )


def _set_child_tables(doc, data):
    """Replace guardians / siblings rows from the submitted payload."""

    # ---- Guardians (Student Guardian child table) ----
    if "guardians" in data:
        doc.set("guardians", [])
        for row in data.get("guardians") or []:
            if not isinstance(row, dict):
                continue
            guardian = _clean(row, "guardian")
            relation = _clean(row, "relation")
            if not guardian and not relation:
                continue
            if guardian and not frappe.db.exists("Guardian", guardian):
                frappe.throw(
                    f"Guardian '{guardian}' does not exist.",
                    title="Invalid Guardian",
                )
            entry = {"relation": relation or ""}
            if guardian:
                entry["guardian"] = guardian
                # guardian_name is a read-only fetch_from field; fill it
                # server-side so the required child field is satisfied.
                entry["guardian_name"] = frappe.db.get_value(
                    "Guardian", guardian, "guardian_name"
                )
            doc.append("guardians", entry)

    # ---- Siblings (Student Sibling child table) ----
    if "siblings" in data:
        doc.set("siblings", [])
        for row in data.get("siblings") or []:
            if not isinstance(row, dict):
                continue
            full_name = _clean(row, "full_name")
            if not full_name:
                continue
            entry = {"full_name": full_name}
            for fieldname in SIBLING_FIELDS:
                if fieldname == "full_name":
                    continue
                value = _clean(row, fieldname)
                if value is not None:
                    entry[fieldname] = value
            doc.append("siblings", entry)


@frappe.whitelist(methods=["POST"])
def save_applicant(payload=None):
    """Create or update a Student Applicant (staff only).

    ``application_status`` is deliberately NOT accepted here - it is
    changed only by the Approve / Reject / Enroll actions so the
    installed workflow cannot be bypassed.
    """

    require_staff()

    data = _payload(payload)
    if not isinstance(data, dict):
        frappe.throw("Invalid request payload.")

    name = _clean(data, "name")
    creating = not name

    _validate_required(data, creating)

    if creating:
        doc = frappe.new_doc("Student Applicant")
        # Start every new applicant at "Applied" - the workflow entry
        # state the Education form's Approve/Reject buttons expect.
        doc.application_status = "Applied"
    else:
        if not frappe.db.exists("Student Applicant", name):
            frappe.throw("Student Applicant not found.")
        doc = frappe.get_doc("Student Applicant", name)
        # Preserve the current workflow status; never take it from the
        # client on a general save.
        doc.application_status = doc.application_status or "Applied"

    # Scalar fields.
    for fieldname in SCALAR_FIELDS:
        if not frappe.get_meta("Student Applicant").has_field(fieldname):
            continue
        if fieldname in data:
            value = _clean(data, fieldname)
            if value is not None:
                doc.set(fieldname, value)
            else:
                doc.set(fieldname, None)

    # Naming series: only accept values the installed Select field allows.
    if "naming_series" in data:
        series = _clean(data, "naming_series")
        meta = frappe.get_meta("Student Applicant")
        series_field = meta.get_field("naming_series")
        allowed = [
            opt.strip()
            for opt in (series_field.options or "").split("\n")
            if opt.strip()
        ] if series_field else []
        if series and allowed and series not in allowed:
            frappe.throw(
                "Invalid naming series.", title="Invalid naming series"
            )
        if series:
            doc.naming_series = series

    # Paid: staff may mark the application fee as received. The
    # Education controller only ever sets this through
    # ``on_payment_authorized`` (payment gateway), so a staff save is the
    # only manual path - exactly like editing the Check field in Desk.
    if "paid" in data:
        paid_value = data.get("paid")
        doc.paid = 1 if paid_value in (1, "1", True) else 0

    # Date-of-Birth guard (mirrors the Education controller, which also
    # validates this - we surface a clear message before the round-trip).
    dob = _clean(data, "date_of_birth")
    if dob and getdate(dob) >= getdate():
        frappe.throw(
            "Date of Birth must be strictly before today.",
            title="Invalid Date of Birth",
        )

    # Academic Term must belong to the chosen Academic Year.
    academic_year = _clean(data, "academic_year")
    academic_term = _clean(data, "academic_term")
    if academic_year and academic_term:
        term_year = frappe.db.get_value("Academic Term", academic_term, "academic_year")
        if term_year and term_year != academic_year:
            frappe.throw(
                f"Academic Term '{academic_term}' does not belong to "
                f"Academic Year '{academic_year}'.",
                title="Invalid Academic Term",
            )

    _set_child_tables(doc, data)

    doc.flags.ignore_permissions = True
    doc.save()

    return {
        "success": True,
        "name": doc.name,
        "application_status": doc.application_status,
        "message": f"Applicant {doc.name} was saved.",
    }


# =========================================================
# WORKFLOW ACTIONS (Approve / Reject / Enroll)
# =========================================================


@frappe.whitelist(methods=["POST"])
def set_applicant_status(name=None, status=None):
    """Approve or Reject an applicant (staff only).

    Mirrors the Education Desk buttons:

    * Approve: Applied -> Approved, Rejected -> Approved.
    * Reject:  Applied -> Rejected, Approved -> Rejected.
    """

    require_staff()

    if not name or not frappe.db.exists("Student Applicant", name):
        frappe.throw("Student Applicant not found.")

    if status not in ("Approved", "Rejected"):
        frappe.throw("Status must be Approved or Rejected.")

    doc = frappe.get_doc("Student Applicant", name)
    current = doc.application_status or "Applied"

    if status == "Approved":
        if current not in ("Applied", "Rejected"):
            frappe.throw(
                f"An applicant with status '{current}' cannot be approved."
            )
    elif status == "Rejected":
        if current not in ("Applied", "Approved"):
            frappe.throw(
                f"An applicant with status '{current}' cannot be rejected."
            )

    doc.application_status = status
    doc.flags.ignore_permissions = True
    doc.save()

    return {
        "success": True,
        "name": name,
        "application_status": status,
        "message": f"Applicant {name} is now {status}.",
    }


@frappe.whitelist(methods=["POST"])
def enroll_applicant(name=None):
    """Enroll an Approved applicant into a Student (staff only).

    Reproduces the installed Education ``enroll_student`` flow:

    1. Map the applicant onto a new Student (``name`` -> ``student_applicant``).
    2. Create the matching Program Enrollment.
    3. Mark the applicant ``Admitted``.

    The Student/User linking follows the supported workflow (the
    Education controller creates the portal User; we then guarantee the
    Student role + link like the Students page does). Duplicate Students
    and duplicate enrollments are prevented.
    """

    require_staff()

    if not name or not frappe.db.exists("Student Applicant", name):
        frappe.throw("Student Applicant not found.")

    applicant = frappe.get_doc("Student Applicant", name)

    if (applicant.application_status or "") != "Approved":
        frappe.throw(
            "Only Approved applicants can be enrolled. "
            "Approve the applicant first.",
            title="Not approved",
        )

    if frappe.db.exists("Student", {"student_applicant": name}):
        frappe.throw(
            "This applicant has already been enrolled as a Student.",
            title="Already enrolled",
        )

    if not applicant.student_email_id:
        frappe.throw(
            "Student Email Address is required before enrolling.",
            title="Missing email",
        )

    email = applicant.student_email_id
    if frappe.db.exists("Student", {"student_email_id": email}):
        frappe.throw(
            "A Student with this email address already exists.",
            title="Duplicate student",
        )

    # --------------------------------------------------
    # Pre-create the portal User (if missing) BEFORE the Student
    # insert. Education's validate_user would otherwise create it and
    # its User.on_update cache wipe would break the Customer grant below.
    # --------------------------------------------------

    _precreate_portal_user(
        applicant.first_name, applicant.last_name, email, applicant.gender
    )

    # --------------------------------------------------
    # Map Applicant -> Student (same field_map as Education).
    # --------------------------------------------------

    student = get_mapped_doc(
        "Student Applicant",
        name,
        {
            "Student Applicant": {
                "doctype": "Student",
                "field_map": {"name": "student_applicant"},
            }
        },
        ignore_permissions=True,
    )

    # The Education Student controller creates/updates an ERPNext
    # Customer without ignore_permissions, so elevate Customer write
    # for this request (same request-scoped grant the Students page
    # uses - never frappe.set_user(), which corrupts the session).
    with _elevated_for_customer_write():
        student.flags.ignore_permissions = True
        student.insert(ignore_permissions=True)

    # --------------------------------------------------
    # Program Enrollment (submitted, matching the applicant).
    # --------------------------------------------------

    enrollment = frappe.new_doc("Program Enrollment")
    enrollment.student = student.name
    enrollment.student_name = student.student_name
    enrollment.student_category = applicant.student_category
    enrollment.program = applicant.program
    enrollment.academic_year = applicant.academic_year
    enrollment.academic_term = applicant.academic_term
    enrollment.enrollment_date = today()
    enrollment.flags.ignore_permissions = True
    enrollment.insert()
    # on_submit creates Course Enrollment docs without ignore_permissions,
    # so keep the request-scoped grant active for the submit as well.
    with _elevated_for_customer_write():
        enrollment.flags.ignore_permissions = True
        enrollment.submit()

    # --------------------------------------------------
    # Guarantee the Student is linked to a usable portal login.
    # --------------------------------------------------

    linked_user = _ensure_portal_user(student)

    # --------------------------------------------------
    # Mark the applicant Admitted (workflow terminal state).
    # --------------------------------------------------

    frappe.db.set_value(
        "Student Applicant", name, "application_status", "Admitted"
    )

    return {
        "success": True,
        "applicant": name,
        "student": student.name,
        "student_name": student.student_name,
        "enrollment": enrollment.name,
        "linked_user": linked_user,
        "application_status": "Admitted",
        "message": (
            f"{student.student_name} was enrolled as {student.name}."
        ),
    }
