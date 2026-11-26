import frappe

MESSAGES_PAGE = "messages"
EXPECTED_MODULE = "Backend Custom App"


def execute():
    page = frappe.db.get_value(
        "Page",
        MESSAGES_PAGE,
        ["name", "module", "standard", "page_name"],
        as_dict=True,
    )

    if not page:
        doc = frappe.get_doc({
            "doctype": "Page",
            "name": MESSAGES_PAGE,
            "page_name": MESSAGES_PAGE,
            "module": EXPECTED_MODULE,
            "standard": "Yes",
            "title": "Messages",
            "icon": "chat",
            "roles": [
                {"role": "Administrator"},
                {"role": "System Manager"},
                {"role": "Education Manager"},
                {"role": "Academics User"},
                {"role": "Instructor"},
            ],
        })
        doc.insert(ignore_permissions=True)
    else:
        values = {}
        if page.module != EXPECTED_MODULE:
            values["module"] = EXPECTED_MODULE
        if page.standard != "Yes":
            values["standard"] = "Yes"
        if page.page_name != MESSAGES_PAGE:
            values["page_name"] = MESSAGES_PAGE
        if values:
            frappe.db.set_value("Page", MESSAGES_PAGE, values)

    duplicates = frappe.get_all("Page", filters={"page_name": MESSAGES_PAGE}, pluck="name")
    i = 0
    while i < len(duplicates):
        if duplicates[i] != MESSAGES_PAGE:
            frappe.delete_doc("Page", duplicates[i], ignore_permissions=True)
        i = i + 1

    frappe.db.commit()
    frappe.clear_cache()
