import frappe

SIDEBAR_NAMES = ["Education", "education"]

ITEM_FIELDS = [
    "label",
    "link_type",
    "icon",
    "type",
    "link_to",
    "child",
    "navigate_to_tab",
    "url",
    "filters",
    "route_options",
    "collapsible",
    "indent",
    "keep_closed",
    "show_arrow",
]

TOP_ITEMS = [
    {"label": "Dashboard", "link_type": "Page", "link_to": "education-dashboard", "icon": "list"},
    {"label": "Messages", "link_type": "Page", "link_to": "messages", "icon": "message-circle"},
    {"label": "Home", "link_type": "Workspace", "link_to": "Education", "icon": "home"},
]

SETTINGS_ITEM = {
    "label": "Settings",
    "link_type": "DocType",
    "link_to": "Education Settings",
    "icon": "setting",
}

SECTIONS = [
    {
        "label": "Admissions",
        "icon": "graduation-cap",
        "items": [
            {"label": "Student Applicant", "link_type": "DocType", "link_to": "Student Applicant"},
            {"label": "Student Admission", "link_type": "DocType", "link_to": "Student Admission"},
        ],
    },
    {
        "label": "Student Management",
        "icon": "users-round",
        "items": [
            {"label": "Student", "link_type": "DocType", "link_to": "Student"},
            {"label": "Section/Division", "link_type": "DocType", "link_to": "Student Group"},
            {"label": "Student Category", "link_type": "DocType", "link_to": "Student Category"},
            {"label": "Student Batch Name", "link_type": "DocType", "link_to": "Student Batch Name"},
            {"label": "Guardian", "link_type": "DocType", "link_to": "Guardian"},
            {"label": "Class Enrollment", "link_type": "DocType", "link_to": "Program Enrollment"},
            {"label": "Subject Enrollment", "link_type": "DocType", "link_to": "Course Enrollment"},
            {"label": "Student Log", "link_type": "DocType", "link_to": "Student Log"},
        ],
    },
    {
        "label": "Academics",
        "icon": "book-open-text",
        "items": [
            {"label": "Academic Year", "link_type": "DocType", "link_to": "Academic Year"},
            {"label": "Academic Term", "link_type": "DocType", "link_to": "Academic Term"},
            {"label": "Standard/Class", "link_type": "DocType", "link_to": "Program"},
            {"label": "Subjects", "link_type": "DocType", "link_to": "Course"},
            {"label": "Course Schedule", "link_type": "DocType", "link_to": "Course Schedule"},
            {"label": "Course Activity", "link_type": "DocType", "link_to": "Course Activity"},
        ],
    },
    {
        "label": "Assessment",
        "icon": "clipboard-check",
        "items": [
            {"label": "Assessment Plan", "link_type": "DocType", "link_to": "Assessment Plan"},
            {"label": "Assessment Criteria", "link_type": "DocType", "link_to": "Assessment Criteria"},
            {"label": "Assessment Group", "link_type": "DocType", "link_to": "Assessment Group"},
            {"label": "Assessment Result", "link_type": "DocType", "link_to": "Assessment Result"},
            {"label": "Grading Scale", "link_type": "DocType", "link_to": "Grading Scale"},
        ],
    },
    {
        "label": "Fee Management",
        "icon": "money",
        "items": [
            {"label": "Fees", "link_type": "DocType", "link_to": "Fees"},
            {"label": "Fee Structure", "link_type": "DocType", "link_to": "Fee Structure"},
            {"label": "Fee Category", "link_type": "DocType", "link_to": "Fee Category"},
            {"label": "Fee Schedule", "link_type": "DocType", "link_to": "Fee Schedule"},
            {"label": "Sales Invoice", "link_type": "DocType", "link_to": "Sales Invoice"},
            {"label": "Sales Order", "link_type": "DocType", "link_to": "Sales Order"},
        ],
    },
    {
        "label": "Attendance",
        "icon": "calendar-check",
        "items": [
            {"label": "Student Attendance", "link_type": "DocType", "link_to": "Student Attendance"},
            {"label": "Student Leave Application", "link_type": "DocType", "link_to": "Student Leave Application"},
        ],
    },
    {
        "label": "Tools",
        "icon": "tool",
        "items": [
            {"label": "Program Enrollment", "link_type": "DocType", "link_to": "Program Enrollment"},
            {"label": "Student Group Creation Tool", "link_type": "DocType", "link_to": "Student Group Creation Tool"},
            {"label": "Student Attendance Tool", "link_type": "DocType", "link_to": "Student Attendance Tool"},
            {"label": "Student Report Generation Tool", "link_type": "DocType", "link_to": "Student Report Generation Tool"},
            {"label": "Assessment Result Tool", "link_type": "DocType", "link_to": "Assessment Result Tool"},
            {"label": "Course Scheduling Tool", "link_type": "DocType", "link_to": "Course Scheduling Tool"},
        ],
    },
    {
        "label": "Setup",
        "icon": "settings",
        "items": [
            {"label": "Teachers/lecturer", "link_type": "DocType", "link_to": "Instructor"},
            {"label": "Room", "link_type": "DocType", "link_to": "Room"},
        ],
    },
    {
        "label": "Reports",
        "icon": "file-text",
        "items": [
            {"label": "Course wise Assessment Report", "link_type": "Report", "link_to": "Course wise Assessment Report"},
            {"label": "Final Assessment Grades", "link_type": "Report", "link_to": "Final Assessment Grades"},
            {"label": "Assessment Plan Status", "link_type": "Report", "link_to": "Assessment Plan Status"},
            {"label": "Student Fee Collection", "link_type": "Report", "link_to": "Student Fee Collection"},
            {"label": "Program wise Fee Collection", "link_type": "Report", "link_to": "Program wise Fee Collection"},
            {"label": "Absent Student Report", "link_type": "Report", "link_to": "Absent Student Report"},
            {"label": "Student Monthly Attendance Sheet", "link_type": "Report", "link_to": "Student Monthly Attendance Sheet"},
            {"label": "Student Batch-Wise Attendance", "link_type": "Report", "link_to": "Student Batch-Wise Attendance"},
            {"label": "Student and Guardian Contact Details", "link_type": "Report", "link_to": "Student and Guardian Contact Details"},
        ],
    },
]


def get_sidebar():
    for name in SIDEBAR_NAMES:
        if frappe.db.exists("Workspace Sidebar", name):
            return frappe.get_doc("Workspace Sidebar", name)
    return None


def target_exists(link_type, link_to):
    if not link_to:
        return False
    if link_type in ["DocType", "Report", "Page", "Workspace"]:
        return bool(frappe.db.exists(link_type, link_to))
    return True


def item_key(row):
    if row.get("type") == "Section Break":
        return ("Section Break", row.get("label"), None, None)
    return ("Link", row.get("link_type"), row.get("link_to"), None)


def copy_item(row):
    data = row.as_dict()
    item = {}
    for field in ITEM_FIELDS:
        if field in data:
            item[field] = data.get(field)
    return item


def make_link(item, child, icon=None):
    return {
        "label": item.get("label"),
        "link_type": item.get("link_type"),
        "icon": icon,
        "type": "Link",
        "link_to": item.get("link_to"),
        "child": child,
        "navigate_to_tab": None,
        "url": None,
        "filters": "[]",
        "route_options": None,
        "collapsible": 1,
        "indent": 0,
        "keep_closed": 0,
        "show_arrow": 0,
    }


def make_section(label, icon):
    return {
        "label": label,
        "link_type": "DocType",
        "icon": icon,
        "type": "Section Break",
        "link_to": None,
        "child": 0,
        "navigate_to_tab": None,
        "url": None,
        "filters": "[]",
        "route_options": None,
        "collapsible": 1,
        "indent": 1,
        "keep_closed": 0,
        "show_arrow": 0,
    }


def execute():
    sidebar = get_sidebar()
    if not sidebar:
        return

    desired_keys = set()
    for item in TOP_ITEMS + [SETTINGS_ITEM]:
        desired_keys.add(item_key(item))
    for section in SECTIONS:
        desired_keys.add(item_key({"type": "Section Break", "label": section["label"]}))
        for item in section["items"]:
            desired_keys.add(item_key(item))

    section_icons = {}
    top_icons = {}
    for row in sidebar.items:
        if row.type == "Section Break":
            section_icons[row.label] = row.icon or section_icons.get(row.label)
        elif row.icon and row.label not in top_icons:
            top_icons[row.label] = row.icon

    extras = {}
    current_section = None
    unknown_section = False
    for row in sidebar.items:
        if row.type == "Section Break":
            current_section = row.label
            unknown_section = item_key(row) not in desired_keys
            if unknown_section:
                extras.setdefault("__unknown__", []).append(copy_item(row))
            continue
        if item_key(row) in desired_keys:
            continue
        if unknown_section:
            extras.setdefault("__unknown__", []).append(copy_item(row))
        else:
            extras.setdefault(current_section or "", []).append(copy_item(row))

    new_items = []
    for item in TOP_ITEMS:
        if target_exists(item["link_type"], item["link_to"]):
            new_items.append(make_link(item, 0, top_icons.get(item["label"]) or item.get("icon")))
    for item in extras.get("", []):
        new_items.append(item)

    for section in SECTIONS:
        new_items.append(make_section(section["label"], section_icons.get(section["label"]) or section.get("icon")))
        for item in section["items"]:
            if target_exists(item["link_type"], item["link_to"]):
                new_items.append(make_link(item, 1))
        for item in extras.get(section["label"], []):
            new_items.append(item)
        if section["label"] == "Setup" and target_exists(SETTINGS_ITEM["link_type"], SETTINGS_ITEM["link_to"]):
            new_items.append(make_link(SETTINGS_ITEM, 0, top_icons.get("Settings") or SETTINGS_ITEM.get("icon")))

    for item in extras.get("__unknown__", []):
        new_items.append(item)

    for index, item in enumerate(new_items, 1):
        item["idx"] = index

    sidebar.set("items", [])
    for item in new_items:
        sidebar.append("items", item)
    sidebar.save(ignore_permissions=True)
    frappe.db.commit()
