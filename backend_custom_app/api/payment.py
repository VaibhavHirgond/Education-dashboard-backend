import frappe
import requests


def send_whatsapp_message(phone_number, message):
    try:
        phone_number_id = frappe.conf.get("whatsapp_phone_number_id")
        access_token = frappe.conf.get("whatsapp_access_token")

        if not phone_number or not phone_number_id or not access_token:
            return

        # Remove spaces, +, -, and brackets from phone number
        phone_number = (
            str(phone_number)
            .replace("+", "")
            .replace(" ", "")
            .replace("-", "")
            .replace("(", "")
            .replace(")", "")
        )

        # If a 10-digit Indian number is stored, add India country code
        if len(phone_number) == 10:
            phone_number = "91" + phone_number

        url = f"https://graph.facebook.com/v24.0/{phone_number_id}/messages"

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }

        payload = {
            "messaging_product": "whatsapp",
            "to": phone_number,
            "type": "text",
            "text": {
                "body": message
            }
        }

        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=15
        )

        if not response.ok:
            frappe.log_error(
                title="WhatsApp Message Failed",
                message=response.text
            )

    except Exception:
        frappe.log_error(
            title="WhatsApp Notification Error",
            message=frappe.get_traceback()
        )


def payment_entry_submitted(doc, method):
    # Process only Receive payments
    if doc.payment_type != "Receive":
        return

    # Find the Sales Invoice paid by this Payment Entry
    invoice_name = None

    for reference in doc.references:
        if reference.reference_doctype == "Sales Invoice":
            invoice_name = reference.reference_name
            break

    if not invoice_name:
        return

    # Get the Sales Invoice
    invoice = frappe.get_doc("Sales Invoice", invoice_name)

    # Get the Student
    if not invoice.student:
        return

    from backend_custom_app.api.student_notifications import payment_entry_submitted

    payment_entry_submitted(doc, invoice)

    student = frappe.get_doc("Student", invoice.student)

    # ---------------------------------------------------------
    # STUDENT EMAIL
    # ---------------------------------------------------------

    student_email = student.student_email_id

    if student_email:
        frappe.sendmail(
            recipients=[student_email],
            subject="Fee Payment Successful",
            message=f"""
                <p>Dear {student.student_name},</p>

                <p>Your fee payment has been successfully received.</p>

                <p>
                    <b>Amount:</b> ₹{invoice.grand_total}<br>
                    <b>Sales Invoice:</b> {invoice.name}
                </p>

                <p>Thank you.</p>
            """
        )

    # ---------------------------------------------------------
    # STUDENT WHATSAPP
    # ---------------------------------------------------------

    student_mobile = student.student_mobile_number

    if student_mobile:
        send_whatsapp_message(
            student_mobile,
            f"""Dear {student.student_name},

Your fee payment has been successfully received.

Amount: ₹{invoice.grand_total}
Sales Invoice: {invoice.name}

Thank you."""
        )

    # ---------------------------------------------------------
    # FIND STUDENT GROUPS
    # ---------------------------------------------------------

    groups = frappe.get_all(
        "Student Group Student",
        filters={"student": student.name},
        fields=["parent"]
    )

    instructor_emails = set()
    instructor_mobiles = set()

    # ---------------------------------------------------------
    # FIND INSTRUCTORS
    # ---------------------------------------------------------

    for group in groups:
        instructors = frappe.get_all(
            "Student Group Instructor",
            filters={"parent": group.parent},
            fields=["instructor"]
        )

        for instructor_row in instructors:
            instructor = instructor_row.instructor

            # Instructor email
            email = frappe.db.get_value(
                "Instructor",
                instructor,
                "custom_instructor_email"
            )

            if email:
                instructor_emails.add(email)

            # Instructor WhatsApp number
            mobile = frappe.db.get_value(
                "Instructor",
                instructor,
                "custom_instructor_mobile_number"
            )

            if mobile:
                instructor_mobiles.add(mobile)

    # ---------------------------------------------------------
    # INSTRUCTOR EMAIL
    # ---------------------------------------------------------

    for email in instructor_emails:
        frappe.sendmail(
            recipients=[email],
            subject="Student Fee Payment Received",
            message=f"""
                <p>Hello,</p>

                <p>
                    The fee payment for student
                    <b>{student.student_name}</b> has been received.
                </p>

                <p>
                    <b>Amount:</b> ₹{invoice.grand_total}<br>
                    <b>Sales Invoice:</b> {invoice.name}
                </p>
            """
        )

    # ---------------------------------------------------------
    # INSTRUCTOR WHATSAPP
    # ---------------------------------------------------------

    for mobile in instructor_mobiles:
        send_whatsapp_message(
            mobile,
            f"""Hello,

The fee payment for student {student.student_name} has been received.

Amount: ₹{invoice.grand_total}
Sales Invoice: {invoice.name}"""
        )
