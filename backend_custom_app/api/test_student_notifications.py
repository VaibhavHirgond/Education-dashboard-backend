from unittest.mock import Mock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from backend_custom_app.api import student_notifications


class TestStudentNotifications(FrappeTestCase):
    def setUp(self):
        self.original_user = frappe.session.user
        frappe.session.user = "learner@example.com"
        self.addCleanup(setattr, frappe.session, "user", self.original_user)

    def test_list_is_scoped_to_current_user_and_education_records(self):
        row = frappe._dict(
            name="NL-1",
            title="New grade available",
            description="Grade <strong>A</strong>",
            type="Alert",
            creation="2026-10-15 10:00:00",
            read=0,
            document_type="Assessment Result",
            document_name="RES-1",
        )
        with (
            patch.object(student_notifications, "_current_student"),
            patch.object(frappe, "get_all", return_value=[row]) as get_all,
            patch.object(frappe.db, "count", return_value=1),
        ):
            result = student_notifications.get_student_notifications()

        self.assertEqual(get_all.call_args.kwargs["filters"], {
            "for_user": "learner@example.com",
            "app": "education",
        })
        self.assertEqual(result["unread_count"], 1)
        self.assertEqual(result["notifications"][0]["category"], "Grades")
        self.assertEqual(result["notifications"][0]["description"], "Grade A")
        self.assertEqual(result["notifications"][0]["destination"], "/grades")

    def test_mark_read_cannot_modify_another_users_notification(self):
        with (
            patch.object(student_notifications, "_current_student"),
            patch.object(frappe.db, "exists", return_value=False) as exists,
            patch.object(frappe.db, "set_value") as set_value,
        ):
            with self.assertRaises(frappe.PermissionError):
                student_notifications.mark_student_notification_read("OTHER-1")

        self.assertEqual(exists.call_args.args[1], {
            "for_user": "learner@example.com",
            "app": "education",
            "name": "OTHER-1",
        })
        set_value.assert_not_called()

    def test_mark_read_updates_only_the_owned_notification(self):
        with (
            patch.object(student_notifications, "_current_student"),
            patch.object(frappe.db, "exists", return_value=True),
            patch.object(frappe.db, "set_value") as set_value,
        ):
            result = student_notifications.mark_student_notification_read("OWN-1")

        self.assertTrue(result["success"])
        self.assertEqual(set_value.call_args.args[1], {
            "for_user": "learner@example.com",
            "app": "education",
            "name": "OWN-1",
        })
        self.assertEqual(set_value.call_args.args[2:4], ("read", 1))

    def test_event_creation_is_deduplicated_per_student_and_record(self):
        student = frappe._dict(
            user="learner@example.com",
            student_email_id="learner@example.com",
            enabled=1,
        )
        notification = frappe._dict(name="NL-1", insert=Mock())
        with (
            patch.object(frappe.db, "get_value", return_value=student),
            patch.object(frappe.db, "exists", side_effect=[True, False, True, True]),
            patch.object(frappe, "get_doc", return_value=notification) as get_doc,
        ):
            first = student_notifications.create_student_notification(
                "STU-1", "Attendance marked: Present", "Present", "Student Attendance", "ATT-1"
            )
            second = student_notifications.create_student_notification(
                "STU-1", "Attendance marked: Present", "Present", "Student Attendance", "ATT-1"
            )

        self.assertEqual(first, "NL-1")
        self.assertIsNone(second)
        get_doc.assert_called_once()
        notification.insert.assert_called_once_with(ignore_permissions=True)

    def test_attendance_event_uses_submitted_record_data(self):
        attendance = frappe._dict(
            docstatus=1,
            student="STU-1",
            status="Present",
            date="2026-10-15",
            course_schedule=None,
            name="ATT-1",
        )
        with patch.object(student_notifications, "create_student_notification") as create:
            student_notifications.attendance_submitted(attendance)

        create.assert_called_once_with(
            "STU-1",
            "Attendance marked: Present",
            "Attendance for 2026-10-15 is marked Present.",
            "Student Attendance",
            "ATT-1",
        )

    def test_attendance_update_notifies_only_when_status_changes(self):
        attendance = frappe._dict(
            docstatus=1,
            student="STU-1",
            status="Present",
            date="2026-10-15",
            course_schedule=None,
            name="ATT-1",
        )
        attendance.get_doc_before_save = Mock(return_value=frappe._dict(status="Absent"))
        with patch.object(student_notifications, "attendance_submitted") as submitted:
            student_notifications.attendance_updated(attendance)

        submitted.assert_called_once_with(attendance, None)

    def test_payment_event_uses_submitted_invoice_allocation(self):
        payment = frappe._dict(
            docstatus=1,
            paid_from_account_currency="INR",
            references=[
                frappe._dict(
                    reference_doctype="Sales Invoice",
                    reference_name="INV-1",
                    allocated_amount=1250,
                )
            ],
        )
        invoice = frappe._dict(
            docstatus=1,
            student="STU-1",
            name="INV-1",
            currency="INR",
            grand_total=5000,
        )
        with patch.object(student_notifications, "create_student_notification") as create:
            student_notifications.payment_entry_submitted(payment, invoice)

        self.assertIn("INR 1250.0", create.call_args.args[2])
        self.assertNotIn("5000", create.call_args.args[2])

    def test_payment_event_requires_a_positive_invoice_allocation(self):
        payment = frappe._dict(
            docstatus=1,
            references=[
                frappe._dict(
                    reference_doctype="Sales Invoice",
                    reference_name="INV-1",
                    allocated_amount=0,
                )
            ],
        )
        invoice = frappe._dict(
            docstatus=1,
            student="STU-1",
            name="INV-1",
            currency="INR",
            grand_total=5000,
        )
        with patch.object(student_notifications, "create_student_notification") as create:
            student_notifications.payment_entry_submitted(payment, invoice)

        create.assert_not_called()
