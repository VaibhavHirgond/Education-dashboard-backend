import frappe


def execute():
    try:
        if not frappe.db.exists("Workspace Sidebar", "education"):
            return
        doc = frappe.get_doc("Workspace Sidebar", "education")
        new_items = []
        i = 0
        while i < len(doc.items):
            it = doc.items[i]
            if it.link_to == "messages":
                i = i + 1
                continue
            new_items.append(it.as_dict())
            if it.link_to == "education-dashboard":
                new_items.append({
                    "child": 0,
                    "collapsible": 1,
                    "icon": "message-circle",
                    "indent": 0,
                    "keep_closed": 0,
                    "label": "Messages",
                    "link_to": "messages",
                    "link_type": "Page",
                    "show_arrow": 0,
                    "type": "Link",
                    "filters": "[]"
                })
            i = i + 1
        doc.items = []
        for it in new_items:
            doc.append("items", it)
        doc.save(ignore_permissions=True)
        frappe.db.commit()
    except Exception as e:
        frappe.log_error(title="add_messages_to_education_sidebar", message=str(e))
        pass