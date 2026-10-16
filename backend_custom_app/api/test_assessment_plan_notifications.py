from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from backend_custom_app.api import permissions, student_notifications


def _plan(**overrides):
    values = {
        "doctype": "Assessment Plan",
        "docstatus": 1,
        "name": "EDU-ASP-2026-00001",
        "assessment_name": "Test Exam",
        "student_group": "2026-27 CSE - Programming Fundamentals",
        "course": "Programming Fundamentals",
        "assessment_group": "Internal Assessment",
        "schedule_date": "2026-10-16",
        "from_time": "10:00:00",
        "to_time": "12:00:00",
        "room": None,
        "maximum_assessment_score": 100.0,
        "academic_year": "2026-27",
        "academic_term": None,
        "examiner": "Nitish",
        "examiner_name": "Nitish",
    }
    values.update(overrides)
    return frappe._dict(values)


class TestAssessmentPlanNotifications(FrappeTestCase):
    def test_draft_plans_never_notify(self):
        plan = _plan(docstatus=0)
        with (
            patch.object(student_notifications, "_plan_students") as get_students,
            patch.object(student_notifications, "create_student_notification") as create,
        ):
            student_notifications.assessment_plan_submitted(plan)

        get_students.assert_not_called()
        create.assert_not_called()

    def test_only_active_students_of_the_plans_group_are_notified(self):
        plan = _plan()
        with (
            patch.object(
                student_notifications,
                "_plan_students",
                return_value=["EDU-STU-1", "EDU-STU-3"],
            ) as get_students,
            patch.object(student_notifications, "_already_notified", return_value=False),
            patch.object(student_notifications, "_student_user", return_value="s1@example.com"),
            patch.object(student_notifications, "create_student_notification") as create,
            patch.object(student_notifications, "_send_assessment_plan_email") as send_email,
        ):
            student_notifications.assessment_plan_submitted(plan)

        get_students.assert_called_once_with("2026-27 CSE - Programming Fundamentals")
        self.assertEqual(create.call_count, 2)
        self.assertEqual(send_email.call_count, 2)

        args = create.call_args_list[0].args
        self.assertEqual(args[0], "EDU-STU-1")
        self.assertEqual(args[1], "New Assessment Scheduled: Test Exam")
        self.assertEqual(args[3], "Assessment Plan")
        self.assertEqual(args[4], "EDU-ASP-2026-00001")

    def test_message_contains_real_fields_and_never_a_validity_date(self):
        plan = _plan()
        with patch.object(
            student_notifications,
            "_student_group_label",
            return_value="2026-27 CSE - Programming Fundamentals",
        ):
            message = student_notifications._assessment_plan_message(plan)

        self.assertIn("Assessment: Test Exam", message)
        self.assertIn("Course: Programming Fundamentals", message)
        self.assertIn("Student Group: 2026-27 CSE - Programming Fundamentals", message)
        self.assertIn("Assessment Group: Internal Assessment", message)
        self.assertIn("Scheduled Date:", message)
        self.assertIn("Maximum Score: 100", message)
        self.assertIn("Academic Year: 2026-27", message)
        # The DocType has no validity / end-date field - it must never be invented.
        self.assertNotIn("Valid Until", message)
        self.assertNotIn("End Date", message)

    def test_message_omits_optional_fields_that_are_not_set(self):
        plan = _plan(
            assessment_name="",
            room=None,
            academic_year=None,
            academic_term=None,
            examiner=None,
            examiner_name=None,
        )
        with patch.object(
            student_notifications, "_student_group_label", return_value=plan.student_group
        ):
            message = student_notifications._assessment_plan_message(plan)

        self.assertNotIn("Room:", message)
        self.assertNotIn("Academic Year:", message)
        self.assertNotIn("Examiner:", message)

    def test_existing_notification_blocks_duplicate_delivery(self):
        plan = _plan()
        with (
            patch.object(student_notifications, "_plan_students", return_value=["EDU-STU-1"]),
            patch.object(student_notifications, "_already_notified", return_value=True),
            patch.object(student_notifications, "_student_user", return_value="s1@example.com"),
            patch.object(student_notifications, "create_student_notification") as create,
            patch.object(student_notifications, "_send_assessment_plan_email") as send_email,
        ):
            student_notifications.assessment_plan_submitted(plan)

        create.assert_not_called()
        send_email.assert_not_called()

    def test_student_without_user_is_skipped_and_logged(self):
        plan = _plan()
        with (
            patch.object(
                student_notifications, "_plan_students", return_value=["EDU-STU-NOUSER"]
            ),
            patch.object(student_notifications, "_student_user", return_value=None),
            patch.object(frappe, "log_error") as log_error,
            patch.object(student_notifications, "create_student_notification") as create,
        ):
            student_notifications.assessment_plan_submitted(plan)

        create.assert_not_called()
        log_error.assert_called_once()

    def test_email_failure_is_logged_and_does_not_propagate(self):
        plan = _plan()
        with (
            patch.object(student_notifications, "_plan_students", return_value=["EDU-STU-1"]),
            patch.object(student_notifications, "_already_notified", return_value=False),
            patch.object(student_notifications, "_student_user", return_value="s1@example.com"),
            patch.object(student_notifications, "create_student_notification", return_value="NL-1"),
            patch.object(
                student_notifications.frappe,
                "sendmail",
                side_effect=RuntimeError("SMTP down"),
            ),
            patch.object(frappe, "log_error") as log_error,
        ):
            # Must not raise: a mail failure may not roll back the plan.
            student_notifications.assessment_plan_submitted(plan)

        log_error.assert_called_once()

    def test_empty_group_logs_and_notifies_nobody(self):
        plan = _plan(student_group="Empty Group")
        with (
            patch.object(student_notifications, "_plan_students", return_value=[]),
            patch.object(frappe, "log_error") as log_error,
            patch.object(student_notifications, "create_student_notification") as create,
        ):
            student_notifications.assessment_plan_submitted(plan)

        create.assert_not_called()
        log_error.assert_called_once()

    def test_group_label_falls_back_to_group_name(self):
        with patch.object(
            student_notifications.frappe.db, "get_value", return_value=None
        ):
            label = student_notifications._student_group_label("Some Group")
        self.assertEqual(label, "Some Group")


class TestInstructorAssessmentPlanScope(FrappeTestCase):
    """The has_permission hook must keep instructors inside their groups."""

    def test_instructor_may_touch_only_their_own_groups_plan(self):
        plan = _plan(student_group="Nitish Group")
        with patch.object(
            permissions, "get_instructor_groups", return_value=["Nitish Group"]
        ):
            self.assertTrue(permissions._instructor_allowed(plan, "edu.instructor@example.com"))

    def test_instructor_is_denied_for_a_foreign_group(self):
        plan = _plan(student_group="Someone Else Group")
        with patch.object(
            permissions, "get_instructor_groups", return_value=["Nitish Group"]
        ):
            self.assertFalse(
                permissions._instructor_allowed(plan, "edu.instructor@example.com")
            )

    def test_staff_users_are_not_group_scoped(self):
        plan = _plan(student_group="Any Group")
        # _student_allowed only scopes students and instructors.
        self.assertTrue(permissions._student_allowed(plan, "Administrator"))

    def test_instructor_scope_query_limits_plan_rows_to_own_groups(self):
        with patch.object(
            permissions, "get_instructor_groups", return_value=["G1", "G2"]
        ):
            condition = permissions._instructor_scope_condition(
                "Assessment Plan", "edu.instructor@example.com"
            )

        self.assertIn("`tabAssessment Plan`.`student_group` in", condition)
        self.assertIn("'G1'", condition)
        self.assertIn("'G2'", condition)
