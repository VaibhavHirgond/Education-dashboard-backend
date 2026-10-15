app_name = "backend_custom_app"
app_title = "Backend Custom App"
app_publisher = "Vaibhav Hirgond"
app_description = "Backend Desk Custom Application"
app_email = "vaibhavhirgond2004@gmail.com"
app_license = "mit"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "backend_custom_app",
# 		"logo": "/assets/backend_custom_app/logo.png",
# 		"title": "Backend Custom App",
# 		"route": "/backend_custom_app",
# 		"has_permission": "backend_custom_app.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/backend_custom_app/css/backend_custom_app.css"
# app_include_js = "/assets/backend_custom_app/js/backend_custom_app.js"

# include js, css files in header of web template
# web_include_css = "/assets/backend_custom_app/css/backend_custom_app.css"
# web_include_js = "/assets/backend_custom_app/js/backend_custom_app.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "backend_custom_app/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "backend_custom_app/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# automatically load and sync documents of this doctype from downstream apps
# importable_doctypes = [doctype_1]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "backend_custom_app.utils.jinja_methods",
# 	"filters": "backend_custom_app.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "backend_custom_app.install.before_install"
# after_install = "backend_custom_app.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "backend_custom_app.uninstall.before_uninstall"
# after_uninstall = "backend_custom_app.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "backend_custom_app.utils.before_app_install"
# after_app_install = "backend_custom_app.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "backend_custom_app.utils.before_app_uninstall"
# after_app_uninstall = "backend_custom_app.utils.after_app_uninstall"

# Build
# ------------------
# To hook into the build process

# after_build = "backend_custom_app.build.after_build"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "backend_custom_app.notifications.get_notification_config"

# Permissions
# -----------
# Learners only ever read their own rows through the generic REST
# API; staff and education operators keep full access.

from backend_custom_app.api.permissions import RESTRICTED_FOR_STUDENTS  # noqa: E402

permission_query_conditions = {
    doctype: "backend_custom_app.api.permissions.student_query_conditions"
    for doctype in RESTRICTED_FOR_STUDENTS
}

has_permission = {
    doctype: "backend_custom_app.api.permissions.student_has_permission"
    for doctype in RESTRICTED_FOR_STUDENTS
}

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

doc_events = {
    "Payment Entry": {
        "on_submit": "backend_custom_app.api.payment.payment_entry_submitted"
    },
    "Student Attendance": {
        "on_submit": "backend_custom_app.api.student_notifications.attendance_submitted",
        "on_update_after_submit": "backend_custom_app.api.student_notifications.attendance_updated",
    },
    "Student Leave Application": {
        "on_submit": "backend_custom_app.api.student_notifications.leave_approved",
        "on_cancel": "backend_custom_app.api.student_notifications.leave_rejected",
    },
    "Assessment Result": {
        "on_submit": "backend_custom_app.api.student_notifications.assessment_result_submitted"
    },
}

scheduler_events = {
    "daily": ["backend_custom_app.api.student_notifications.notify_upcoming_classes"]
}
# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"backend_custom_app.tasks.all"
# 	],
# 	"daily": [
# 		"backend_custom_app.tasks.daily"
# 	],
# 	"hourly": [
# 		"backend_custom_app.tasks.hourly"
# 	],
# 	"weekly": [
# 		"backend_custom_app.tasks.weekly"
# 	],
# 	"monthly": [
# 		"backend_custom_app.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "backend_custom_app.install.before_tests"

# Extend DocType Class
# ------------------------------
#
# Specify custom mixins to extend the standard doctype controller.
# extend_doctype_class = {
# 	"Task": "backend_custom_app.custom.task.CustomTaskMixin"
# }

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "backend_custom_app.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "backend_custom_app.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["backend_custom_app.utils.before_request"]
# after_request = ["backend_custom_app.utils.after_request"]

# Job Events
# ----------
# before_job = ["backend_custom_app.utils.before_job"]
# after_job = ["backend_custom_app.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"backend_custom_app.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []

