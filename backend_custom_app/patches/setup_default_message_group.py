import frappe


def execute():
    if frappe.db.exists("Message Group", "Admin & Instructors"):
        return
    doc = frappe.get_doc({
        "doctype": "Message Group",
        "group_name": "Admin & Instructors",
        "group_type": "Staff",
        "is_default": 1,
        "description": "Default group for Administrators and all Instructors"
    })
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
