import frappe

from backend_custom_app.api.messages import MESSAGE_MARKER_PATTERN, is_group_member


def execute():
    rows = frappe.get_all(
        "Notification Log",
        filters={
            "type": "Alert",
            "description": ["like", "%[messages:%]"],
        },
        fields=["name", "description", "for_user"],
        order_by="name asc",
    )
    deleted = 0
    i = 0
    while i < len(rows):
        row = rows[i]
        groups = MESSAGE_MARKER_PATTERN.findall(row.description or "")
        if groups:
            group = groups[0]
            valid = bool(frappe.db.exists("Message Group", group)) and is_group_member(row.for_user, group)
            if not valid:
                frappe.delete_doc("Notification Log", row.name, ignore_permissions=True)
                deleted = deleted + 1
        i = i + 1
    frappe.db.commit()
    print("removed_stale_class_message_notification_logs=" + str(deleted))
