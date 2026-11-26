import frappe

from backend_custom_app.api.messages import ensure_all_class_message_groups


def execute():
    ensure_all_class_message_groups()
    frappe.db.commit()
