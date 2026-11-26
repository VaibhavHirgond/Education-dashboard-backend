import frappe

from backend_custom_app.api.messages import ensure_all_members_group


def execute():
    ensure_all_members_group()
    frappe.db.commit()
