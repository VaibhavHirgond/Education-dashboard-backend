import html
import re

import frappe

from backend_custom_app.api.messages import (
    _message_preview_text,
    _user_display_name,
    message_group_from_notification,
)


MESSAGE_FIELDS = [
    "name",
    "sender",
    "message_text",
    "message_type",
    "is_voice_note",
    "file_name",
    "poll",
    "poll_question",
    "creation",
    "sent_at",
]


def _message_name_from_description(description):
    match = re.search(r"'name':\s*'([^']+)'", html.unescape(description or ""))
    return match.group(1) if match else ""


def _sender_from_subject(subject):
    value = str(subject or "")
    if " from " in value:
        return value.rsplit(" from ", 1)[1].strip()
    return "New message"


def _needs_repair(description):
    value = str(description or "")
    return "[messages:" in value or value.endswith(": New message")


def _message_for_notification(group, sender_name, creation):
    rows = frappe.get_all(
        "Group Message",
        filters={"group": group},
        fields=MESSAGE_FIELDS,
        order_by="creation desc",
        limit_page_length=100,
    )
    i = 0
    while i < len(rows):
        row = rows[i]
        stamp = str(row.sent_at or row.creation or "")
        if _user_display_name(row.sender) == sender_name and stamp <= str(creation):
            return row
        i = i + 1
    return None


def execute():
    rows = frappe.get_all(
        "Notification Log",
        filters={
            "type": "Alert",
            "app": "education",
            "subject": ["like", "New message in %"],
        },
        or_filters=[
            ["link", "like", "/desk/messages%"],
            ["link", "like", "/student-portal#/messages%"],
        ],
        fields=["name", "subject", "description", "link", "creation"],
        order_by="name asc",
    )
    repaired_exact = 0
    repaired_matched = 0
    repaired_generic = 0
    i = 0
    while i < len(rows):
        row = rows[i]
        if not _needs_repair(row.description):
            i = i + 1
            continue
        sender_name = _sender_from_subject(row.subject)
        group = message_group_from_notification(row.description, row.link)
        message_name = _message_name_from_description(row.description)
        doc = None
        if message_name and frappe.db.exists("Group Message", message_name):
            doc = frappe.db.get_value("Group Message", message_name, MESSAGE_FIELDS, as_dict=True)
            if doc:
                repaired_exact = repaired_exact + 1
        if not doc and group:
            doc = _message_for_notification(group, sender_name, row.creation)
            if doc:
                repaired_matched = repaired_matched + 1
        if doc:
            preview = _message_preview_text(doc)
        else:
            preview = "New message"
            repaired_generic = repaired_generic + 1
        description = sender_name + ": " + preview
        frappe.db.set_value("Notification Log", row.name, {
            "description": description,
            "email_content": description,
        }, update_modified=False)
        i = i + 1
    frappe.db.commit()
    print("repaired_exact=" + str(repaired_exact))
    print("repaired_matched=" + str(repaired_matched))
    print("repaired_generic=" + str(repaired_generic))
