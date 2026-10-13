"""Student fee payment support.

Uses the standard ERPNext Payment Request workflow. The payable amount is
always computed server-side from the authenticated student's own outstanding
Sales Invoices; a frontend-supplied amount is never trusted as authoritative.

No invoice is ever marked paid by these endpoints. A Payment Request only
redirects the learner to the configured gateway. The invoice is settled by
ERPNext when the gateway confirms the payment server-side.
"""

import json

import frappe
from frappe.utils import flt

from backend_custom_app.api.permissions import (
    get_student_record,
    is_instructor_portal,
    is_staff,
    is_student_portal,
    require_education_access,
    require_education_scope,
)


OPEN_REQUEST_STATUSES = ("Draft", "Requested", "Initiated", "Partially Paid")


def _payload(payload):
    if isinstance(payload, str):
        try:
            data = json.loads(payload)
        except ValueError:
            data = {}
    else:
        data = payload or {}

    return data if isinstance(data, dict) else {}


def _active_company():
    """Company used for gateway lookup / Payment Request creation."""

    company = frappe.defaults.get_user_default("Company")

    if not company:
        company = frappe.db.get_single_value("Global Defaults", "default_company")

    if not company:
        company = frappe.db.get_default("company")

    if not company:
        company = frappe.db.get_value("Company", {}, "name", limit=1)

    return company


def _resolve_gateway_account():
    """Return the applicable Payment Gateway Account, or None if unconfigured.

    A gateway account is only usable when its Payment Gateway is enabled.
    """

    if not frappe.db.exists("DocType", "Payment Gateway Account"):
        return None

    company = _active_company()

    filters = {}
    if company:
        filters["company"] = company

    accounts = frappe.get_all(
        "Payment Gateway Account",
        filters=filters,
        fields=[
            "name",
            "payment_gateway",
            "company",
            "currency",
            "payment_account",
            "is_default",
        ],
        order_by="is_default desc, modified desc",
        limit_page_length=1,
    )

    if not accounts and filters:
        accounts = frappe.get_all(
            "Payment Gateway Account",
            fields=[
                "name",
                "payment_gateway",
                "company",
                "currency",
                "payment_account",
                "is_default",
            ],
            order_by="is_default desc, modified desc",
            limit_page_length=1,
        )

    if not accounts:
        return None

    account = accounts[0]

    # Only expose gateways that are actually enabled.
    if account.payment_gateway and frappe.db.exists(
        "Payment Gateway", account.payment_gateway
    ):
        status = frappe.db.get_value(
            "Payment Gateway", account.payment_gateway, "gateway_status"
        )
        if status and status != "Enabled":
            return None

    return account


def _config_missing(gateway_account):
    """Human-readable list of what an admin must configure to accept payments."""

    missing = []

    if not gateway_account:
        missing.append(
            "No Payment Gateway Account is configured. Create one in "
            "ERPNext (Settings → Payment Gateway Account) with an enabled "
            "Payment Gateway, a payment account and your company."
        )
        return missing

    if not gateway_account.payment_account:
        missing.append(
            "The Payment Gateway Account has no payment account set."
        )

    return missing


def _eligible_invoices(student):
    """Outstanding, submitted Sales Invoices that belong to this student."""

    if not student:
        return []

    invoices = frappe.get_all(
        "Sales Invoice",
        filters={
            "student": student,
            "docstatus": 1,
            "outstanding_amount": [">", 0],
        },
        fields=[
            "name",
            "customer",
            "grand_total",
            "outstanding_amount",
            "currency",
            "due_date",
        ],
        order_by="due_date asc, posting_date asc",
        limit_page_length=200,
    )

    return [row for row in invoices if flt(row.outstanding_amount) > 0]


def _open_requests_for_invoices(invoice_names):
    """Existing open Payment Requests referencing any of these invoices."""

    if not invoice_names:
        return []

    return frappe.get_all(
        "Payment Request",
        filters={
            "reference_doctype": "Sales Invoice",
            "reference_name": ["in", invoice_names],
            "status": ["in", list(OPEN_REQUEST_STATUSES)],
        },
        fields=["name", "status", "grand_total", "payment_url", "reference_name"],
        order_by="modified desc",
    )


def _require_student():
    """Resolve and return the authenticated student's own record."""

    require_education_scope()

    if is_instructor_portal():
        frappe.throw(
            "Instructors do not have access to fee payments.",
            frappe.PermissionError,
        )

    if not is_student_portal() and not is_staff():
        frappe.throw(
            "Only students can pay their own fees.",
            frappe.PermissionError,
        )

    if is_staff():
        frappe.throw(
            "Fee payments are only available on a student account.",
            frappe.PermissionError,
        )

    record = get_student_record()

    if not record:
        frappe.throw(
            "No student record is linked to this account.",
            frappe.PermissionError,
        )

    return record


@frappe.whitelist(methods=["GET", "POST"])
def get_payment_status(payload=None):
    """Return the authoritative payable state for the signed-in student."""

    record = _require_student()

    student = record.name

    invoices = _eligible_invoices(student)

    outstanding_total = sum(flt(row.outstanding_amount) for row in invoices)

    gateway_account = _resolve_gateway_account()

    open_requests = _open_requests_for_invoices([row.name for row in invoices])

    config_missing = _config_missing(gateway_account)

    can_pay = bool(invoices) and bool(gateway_account) and not config_missing

    return {
        "student": student,
        "student_name": getattr(record, "student_name", "") or "",
        "outstanding_total": outstanding_total,
        "outstanding_invoices": [
            {
                "invoice": row.name,
                "customer": row.customer,
                "outstanding_amount": flt(row.outstanding_amount),
                "due_date": row.due_date,
            }
            for row in invoices
        ],
        "invoice_count": len(invoices),
        "gateway_configured": bool(gateway_account),
        "gateway": gateway_account.payment_gateway if gateway_account else "",
        "gateway_account": gateway_account.name if gateway_account else "",
        "config_missing": config_missing,
        "can_pay": can_pay,
        "open_requests": [
            {
                "payment_request": row.name,
                "status": row.status,
                "payment_url": row.payment_url,
                "invoice": row.reference_name,
            }
            for row in open_requests
        ],
    }


@frappe.whitelist(methods=["POST"])
def create_payment_request(payload=None):
    """Create (or reuse) an ERPNext Payment Request for the student's invoices.

    The amount is always recomputed from the invoices' real outstanding
    balances. Ownership is verified against the authenticated student so one
    learner can never pay or touch another learner's invoices. Duplicate open
    requests are reused instead of recreated.
    """

    record = _require_student()

    student = record.name

    data = _payload(payload)

    requested = data.get("invoices") or []

    if isinstance(requested, str):
        requested = [requested]

    # Always start from the student's real outstanding invoices.
    eligible = _eligible_invoices(student)
    eligible_map = {row.name: row for row in eligible}

    if requested:
        # Narrow to the requested subset, but only after ownership checks.
        chosen = []
        for name in requested:
            invoice = frappe.get_all(
                "Sales Invoice",
                filters={"name": name},
                fields=["name", "student", "docstatus", "outstanding_amount"],
                limit_page_length=1,
            )

            if not invoice:
                frappe.throw(f"Invoice {name} not found.")

            invoice = invoice[0]

            if invoice.student != student:
                frappe.throw(
                    "You can only pay your own fee invoices.",
                    frappe.PermissionError,
                )

            if invoice.docstatus != 1 or flt(invoice.outstanding_amount) <= 0:
                frappe.throw(
                    f"Invoice {name} has no outstanding balance."
                )

            if name not in eligible_map:
                frappe.throw(f"Invoice {name} is not payable.")

            chosen.append(eligible_map[name])
    else:
        chosen = eligible

    if not chosen:
        frappe.throw("There are no outstanding fee invoices to pay.")

    # Payable amount recomputed server-side (never trust a client amount).
    amount = sum(flt(row.outstanding_amount) for row in chosen)

    if amount <= 0:
        frappe.throw("There is no outstanding balance to pay.")

    gateway_account = _resolve_gateway_account()

    config_missing = _config_missing(gateway_account)

    if not gateway_account or config_missing:
        frappe.throw(
            "Online payment is not configured yet. "
            + " ".join(config_missing),
            title="Payment not available",
        )

    invoice_names = [row.name for row in chosen]

    # Duplicate protection: reuse an existing open request for these invoices.
    existing = _open_requests_for_invoices(invoice_names)

    if existing:
        reuse = existing[0]
        return {
            "success": True,
            "reused": True,
            "payment_request": reuse.name,
            "status": reuse.status,
            "payment_url": reuse.payment_url,
            "amount": amount,
            "message": "An existing payment request is already open.",
        }

    customer = chosen[0].customer

    if not customer:
        frappe.throw(
            "This fee invoice has no customer linked, so it cannot be paid online."
        )

    company = _active_company()

    payment_request = frappe.new_doc("Payment Request")
    payment_request.payment_gateway_account = gateway_account.name
    payment_request.party_type = "Customer"
    payment_request.party = customer
    payment_request.reference_doctype = "Sales Invoice"
    payment_request.reference_name = invoice_names[0]
    payment_request.grand_total = amount
    payment_request.currency = chosen[0].currency or gateway_account.currency
    payment_request.company = company or gateway_account.company
    payment_request.payment_gateway = gateway_account.payment_gateway
    if gateway_account.payment_account:
        payment_request.payment_account = gateway_account.payment_account

    user_email = frappe.session.user
    if user_email and user_email != "Guest":
        payment_request.email_to = user_email

    payment_request.flags.ignore_permissions = True
    payment_request.insert(ignore_permissions=True)

    # Submitting lets ERPNext ask the gateway for a payment URL. The invoice
    # is NOT marked paid here - that only happens once the gateway confirms.
    payment_request.flags.ignore_permissions = True
    payment_request.submit()

    frappe.db.commit()

    return {
        "success": True,
        "reused": False,
        "payment_request": payment_request.name,
        "status": payment_request.status,
        "payment_url": payment_request.payment_url,
        "amount": amount,
        "message": "Payment request created.",
    }
