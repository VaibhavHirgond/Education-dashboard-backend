import frappe

from backend_custom_app.api.messages import (
    MESSAGE_NOTIFICATION_APP,
    _group_label,
    _message_marker_groups,
    _notification_link_for,
    _user_display_name,
)


def execute():
    logs = frappe.get_all(
        "Notification Log",
        filters={"description": ["like", "%[messages:%"]},
        fields=[
            "name",
            "title",
            "subject",
            "description",
            "link",
            "app",
            "document_type",
            "document_name",
            "for_user",
            "from_user",
        ],
        order_by="creation desc",
        limit_page_length=0,
    )

    changed = 0
    i = 0
    while i < len(logs):
        log = logs[i]
        groups = _message_marker_groups(log.description)
        if groups:
            group = groups[0]
            link = _notification_link_for(log.for_user, group)
            values = {
                "app": MESSAGE_NOTIFICATION_APP,
                "link": link,
                "document_type": None,
                "document_name": None,
            }
            if log.from_user:
                subject = "New message in " + (_group_label(group) or group) + " from " + _user_display_name(log.from_user)
                values["title"] = subject
                values["subject"] = subject
            needs_change = False
            for key, value in values.items():
                if (log.get(key) or "") != (value or ""):
                    needs_change = True
            if needs_change:
                frappe.db.set_value("Notification Log", log.name, values, update_modified=False)
                changed = changed + 1
        i = i + 1

    if changed:
        frappe.db.commit()
