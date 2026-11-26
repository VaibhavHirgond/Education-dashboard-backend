import frappe

MESSAGE_MARKER_PREFIX = "[messages:"
MESSAGE_NOTIFICATION_LINK = "/desk/messages"


def execute():
    logs = frappe.get_all(
        "Notification Log",
        filters={"description": ["like", "%[messages:%"]},
        fields=["name", "description", "link", "document_type", "document_name"],
        order_by="creation desc",
        limit_page_length=0,
    )

    changed = 0
    i = 0
    while i < len(logs):
        log = logs[i]
        marker_at = (log.description or "").find(MESSAGE_MARKER_PREFIX)
        needs_link = log.link != MESSAGE_NOTIFICATION_LINK
        needs_document = bool(log.document_type or log.document_name)
        if marker_at >= 0 and (needs_link or needs_document):
            frappe.db.set_value(
                "Notification Log",
                log.name,
                {
                    "link": MESSAGE_NOTIFICATION_LINK,
                    "document_type": None,
                    "document_name": None,
                },
                update_modified=False,
            )
            changed = changed + 1
        i = i + 1

    if changed:
        frappe.db.commit()
