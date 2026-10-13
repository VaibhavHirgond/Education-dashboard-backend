"""Invoice PDF downloads for the education portal.

Every download is authorized server-side against the signed-in session:

* staff may download any submitted Sales Invoice they can already see on
  the Fees page,
* a learner may only download a submitted Sales Invoice whose ``student``
  field points at the Student record linked to their own account.

The PDF is produced by Frappe's own print pipeline (the Sales Invoice's
print format), never from frontend data, so the file always matches the
real invoice document.
"""

import re

import frappe

from backend_custom_app.api.permissions import (
    get_student_record,
    is_instructor_portal,
    is_staff,
    is_student_portal,
    require_education_scope,
)


def _invoice_name(value):
    """Normalize the requested invoice identifier."""

    if value is None:
        return ""

    if isinstance(value, (list, tuple, dict)):
        frappe.throw(
            "A single invoice identifier is required.",
            frappe.ValidationError,
        )

    return str(value).strip()


def _resolve_invoice(invoice):
    """Return the Sales Invoice document for a verified identifier."""

    if not invoice:
        frappe.throw(
            "No invoice was selected for download.",
            frappe.ValidationError,
        )

    if not frappe.db.exists("Sales Invoice", invoice):
        frappe.throw(
            f"Invoice {invoice} was not found.",
            frappe.DoesNotExistError,
        )

    return frappe.get_doc("Sales Invoice", invoice)


def _authorize_invoice(doc):
    """Verify the signed-in user may download this invoice."""

    if is_instructor_portal():
        frappe.throw(
            "Instructors do not have access to fee invoices.",
            frappe.PermissionError,
        )

    if is_student_portal():
        record = get_student_record()

        if not record:
            frappe.throw(
                "No student record is linked to this account.",
                frappe.PermissionError,
            )

        # Ownership is decided only by the invoice's own student link;
        # nothing about the student is taken from the client.
        if (doc.get("student") or "") != record.name:
            frappe.throw(
                "You can only download invoices that belong to your own "
                "fee records.",
                frappe.PermissionError,
            )

        return

    if is_staff():
        # Staff already see every student invoice on the Fees page.
        return

    frappe.throw(
        "You do not have permission to download fee invoices.",
        frappe.PermissionError,
    )


def _ensure_downloadable(doc):
    """Only submitted (non-canceled, non-draft) invoices can be printed."""

    if doc.docstatus != 1:
        frappe.throw(
            f"Invoice {doc.name} cannot be downloaded because it is not "
            "submitted. Only submitted invoices have an official invoice PDF.",
            frappe.ValidationError,
        )


@frappe.whitelist(methods=["GET"])
def download_invoice(invoice=None):
    """Return the official Sales Invoice PDF for one payment record.

    Query parameters:
        invoice: the real Sales Invoice name (for example
            ``ACC-SINV-2026-00014``) shown on the Fees page.

    The invoice is always resolved and re-checked on the server for every
    request, so swapping the identifier for another student's invoice is
    rejected with 403.
    """

    require_education_scope()

    doc = _resolve_invoice(_invoice_name(invoice))

    _authorize_invoice(doc)

    _ensure_downloadable(doc)

    # Authorization is fully settled above. Frappe's print pipeline also
    # re-checks DocType permissions, but the portal roles are not granted
    # the desk "print" permission on Sales Invoice, so the print
    # permission check is skipped for this already-authorized request.
    frappe.flags.ignore_print_permissions = True

    try:
        pdf_content = frappe.get_print(
            "Sales Invoice",
            doc.name,
            doc=doc,
            as_pdf=True,
        )
    except frappe.ValidationError:
        raise
    except frappe.PermissionError:
        raise
    except Exception as exc:  # pragma: no cover - defensive logging
        frappe.log_error(
            title="Invoice PDF download failed",
            message=frappe.get_traceback(),
            reference_doctype="Sales Invoice",
            reference_name=doc.name,
        )
        frappe.throw(
            f"The invoice PDF for {doc.name} could not be generated. "
            f"Details: {exc}",
            frappe.ValidationError,
        )
    finally:
        frappe.flags.ignore_print_permissions = False

    if not pdf_content:
        frappe.throw(
            f"The invoice PDF for {doc.name} could not be generated.",
            frappe.ValidationError,
        )

    filename = "Invoice-{name}.pdf".format(
        name=re.sub(r"[/\s]+", "-", doc.name)
    )

    frappe.local.response.filename = filename
    frappe.local.response.filecontent = pdf_content
    frappe.local.response.type = "pdf"
