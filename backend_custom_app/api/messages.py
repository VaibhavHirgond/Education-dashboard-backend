import io
import re
import zipfile
from urllib.parse import parse_qs, quote, urlparse

import frappe
from frappe.utils import cint, get_fullname, now_datetime
from frappe.utils.file_manager import get_content_hash, save_file_on_filesystem


DEFAULT_STAFF_GROUP_NAME = "Admin & Instructors"
DEFAULT_ALL_GROUP_NAME = "All Members"
MESSAGE_NOTIFICATION_TYPE = "Alert"
MESSAGE_NOTIFICATION_APP = "education"
MESSAGE_NOTIFICATION_LINK = "/desk/messages"
MESSAGE_NOTIFICATION_LINKS = ["/desk/messages", "/student-portal#/messages"]
MESSAGE_NOTIFICATION_LINK_PREFIXES = ["/desk/messages%", "/student-portal#/messages%"]
MESSAGE_MARKER_PATTERN = re.compile(r"\[messages:([^\]]+)\]")
SHARED_LINK_PATTERN = re.compile(r"https?://[^\s<]+", re.IGNORECASE)
HTML_TAG_PATTERN = re.compile(r"<[^>]*>")
REMOVED_MEMBER_DOCTYPE = "Message Group Removed Member"
MESSAGE_GROUP_MEMBER_DOCTYPE = "Message Group Member"
MESSAGE_SEND_RULE_AUDIT_DOCTYPE = "Message Send Rule Audit"
SEND_MODE_EVERYONE = "everyone"
SEND_MODE_ADMINS_INSTRUCTORS = "admins_instructors"
SEND_MODE_SELECTED = "selected"
SEND_MODES = [SEND_MODE_EVERYONE, SEND_MODE_ADMINS_INSTRUCTORS, SEND_MODE_SELECTED]
CUSTOM_GROUP_TYPE = "Custom"
TYPING_RATE_LIMIT_SECONDS = 2
MAX_IMAGE_SIZE = 10 * 1024 * 1024
MAX_VIDEO_SIZE = 25 * 1024 * 1024
MAX_AUDIO_SIZE = 10 * 1024 * 1024
MAX_DOCUMENT_SIZE = 15 * 1024 * 1024
AUDIO_WEBM_MIME = "audio/webm"
MESSAGE_TYPES = ["text", "image", "video", "audio", "file", "contact", "poll", "sticker"]
ALLOWED_STICKERS = ["😀", "😂", "😍", "😎", "🥳", "😊", "👍", "👏", "🙏", "❤️", "🎉", "🌟"]
ALLOWED_EXTENSIONS = {
    "image": ["jpg", "jpeg", "png", "gif", "webp"],
    "video": ["mp4", "webm"],
    "audio": ["mp3", "wav", "m4a", "ogg", "webm"],
    "document": ["pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "csv", "zip"],
}
BLOCKED_EXTENSIONS = [
    "exe", "bat", "cmd", "sh", "bash", "js", "mjs", "html", "htm", "svg", "php",
    "py", "pl", "rb", "jar", "app", "com", "scr", "msi", "vbs", "ps1",
]
EXPECTED_MIME = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    "mp4": "video/mp4",
    "webm": "video/webm",
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "m4a": "audio/mp4",
    "ogg": "audio/ogg",
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "ppt": "application/vnd.ms-powerpoint",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "txt": "text/plain",
    "csv": "text/csv",
    "zip": "application/zip",
}


def _message_cache():
    if not hasattr(frappe.local, "message_cache"):
        frappe.local.message_cache = {}
    return frappe.local.message_cache


def _clear_message_cache():
    if hasattr(frappe.local, "message_cache"):
        frappe.local.message_cache = {}


def _require_logged_in_user():
    user = frappe.session.user
    if user == "Guest":
        frappe.throw("Please login.", frappe.PermissionError)
    return user


def _clean_caption(value):
    return str(value or "").strip()


def _shared_links_from_text(value):
    text = str(value or "")
    links = []
    seen = {}
    matches = SHARED_LINK_PATTERN.findall(text)
    i = 0
    while i < len(matches):
        link = str(matches[i] or "")
        while link and ".,!?;:)]}'\"".find(link[-1]) >= 0:
            link = link[:-1]
        if link and not seen.get(link):
            seen[link] = 1
            links.append(link)
        i = i + 1
    return links


def _safe_filename(value):
    name = str(value or "").replace("\\", "/")
    name = name.split("/")[-1]
    cleaned = ""
    i = 0
    while i < len(name):
        ch = name[i]
        if ord(ch) > 31 and ch not in '<>:"/\\|?*':
            cleaned = cleaned + ch
        i = i + 1
    cleaned = cleaned.strip().strip(".")
    if not cleaned:
        cleaned = "file"
    if len(cleaned) > 120:
        ext = _extension(cleaned)
        if ext:
            cleaned = cleaned[:116 - len(ext)] + "." + ext
        else:
            cleaned = cleaned[:120]
    return cleaned


def _extension(name):
    parts = str(name or "").rsplit(".", 1)
    if len(parts) < 2:
        return ""
    return parts[1].lower()


def _site_max_file_size():
    value = frappe.conf.get("max_file_size") or 10485760
    try:
        value = int(value)
    except Exception:
        value = 10485760
    if value <= 0:
        value = 10485760
    return value


def _category_limit(category):
    if category == "image":
        return MAX_IMAGE_SIZE
    if category == "video":
        return MAX_VIDEO_SIZE
    if category == "audio":
        return MAX_AUDIO_SIZE
    return MAX_DOCUMENT_SIZE


def _detect_file(content, ext, declared_mime=""):
    if not content or len(content) < 4:
        frappe.throw("File is empty or invalid.")
    head = content[:512].lower()
    if content[:2].upper() == b"MZ":
        frappe.throw("Executable files are not allowed.")
    if content[:4] == b"\x7fELF":
        frappe.throw("Executable files are not allowed.")
    dangerous = [b"#!/", b"<?php", b"<script", b"<html", b"<!doctype", b"<svg"]
    d = 0
    while d < len(dangerous):
        if dangerous[d] in head:
            frappe.throw("Script files are not allowed.")
        d = d + 1
    detected = ""
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        detected = "image/png"
    elif content.startswith(b"\xff\xd8\xff"):
        detected = "image/jpeg"
    elif content[:6] == b"GIF87a" or content[:6] == b"GIF89a":
        detected = "image/gif"
    elif content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        detected = "image/webp"
    elif content[4:8] == b"ftyp":
        brand = content[8:16]
        if ext == "m4a" and (b"M4A" in brand or b"isom" in brand or b"mp42" in brand):
            detected = "audio/mp4"
        else:
            detected = "video/mp4"
    elif content[:4] == b"\x1aE\xdf\xa3":
        declared = str(declared_mime or "").lower()
        if ext == "webm" and (declared.startswith("audio/") or b"A_OPUS" in content[:4096]):
            detected = AUDIO_WEBM_MIME
        else:
            detected = "video/webm"
    elif content[:3] == b"ID3" or (content[0] == 0xFF and (content[1] & 0xE0) == 0xE0):
        detected = "audio/mpeg"
    elif content[:4] == b"RIFF" and content[8:12] == b"WAVE":
        detected = "audio/wav"
    elif content[:4] == b"OggS":
        detected = "audio/ogg"
    elif content[:5] == b"%PDF-":
        detected = "application/pdf"
    elif content[:4] == b"\xd0\xcf\x11\xe0":
        if ext == "doc":
            detected = "application/msword"
        elif ext == "xls":
            detected = "application/vnd.ms-excel"
        elif ext == "ppt":
            detected = "application/vnd.ms-powerpoint"
    elif content[:2] in (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"):
        if zipfile.is_zipfile(io.BytesIO(content)):
            archive = zipfile.ZipFile(io.BytesIO(content))
            names = archive.namelist()
            if ext == "zip":
                detected = "application/zip"
            elif "[Content_Types].xml" in names:
                if ext == "docx" and "word/document.xml" in names:
                    detected = EXPECTED_MIME["docx"]
                elif ext == "xlsx" and "xl/workbook.xml" in names:
                    detected = EXPECTED_MIME["xlsx"]
                elif ext == "pptx" and "ppt/presentation.xml" in names:
                    detected = EXPECTED_MIME["pptx"]
    elif ext in ("txt", "csv") and b"\x00" not in content:
        try:
            content.decode("utf-8")
            detected = EXPECTED_MIME[ext]
        except Exception:
            frappe.throw("Text file must be valid UTF-8.")
    if not detected:
        frappe.throw("File type is not allowed or does not match its contents.")
    return detected


def _validate_attachment(content, original_name, declared_mime=""):
    name = _safe_filename(original_name)
    ext = _extension(name)
    if ext in BLOCKED_EXTENSIONS:
        frappe.throw("This file type is not allowed.")
    category = ""
    keys = ["image", "video", "audio", "document"]
    k = 0
    while k < len(keys):
        if ext in ALLOWED_EXTENSIONS[keys[k]]:
            category = keys[k]
            if ext != "webm":
                break
            if keys[k] == "audio" or not str(declared_mime or "").lower().startswith("audio/"):
                break
        k = k + 1
    if not category:
        frappe.throw("This file type is not allowed.")
    limit = _category_limit(category)
    site_limit = _site_max_file_size()
    if site_limit < limit:
        limit = site_limit
    if len(content) > limit:
        frappe.throw("File is too large. Maximum allowed size is " + str(int(limit / 1048576)) + " MB.")
    detected = _detect_file(content, ext, declared_mime)
    expected = AUDIO_WEBM_MIME if ext == "webm" and category == "audio" else EXPECTED_MIME.get(ext)
    if detected != expected:
        frappe.throw("File contents do not match the file name.")
    return category, name, detected


def _validate_group_for_send(group, action):
    user = frappe.session.user
    if user == "Guest":
        frappe.throw("Please login.", frappe.PermissionError)
    _ensure_class_groups_for_request()
    ensure_default_staff_group()
    ensure_all_members_group()
    if not group:
        frappe.throw("Group is required.")
    if not frappe.db.exists("Message Group", group):
        frappe.throw("Group not found.", frappe.DoesNotExistError)
    if not is_group_member(user, group):
        frappe.throw("You do not have permission to " + action + " in this group.", frappe.PermissionError)
    allowed, reason = _can_user_send_in_group(user, group)
    if not allowed:
        frappe.throw(reason, frappe.PermissionError)
    return user


def _ensure_class_groups_for_request():
    cache = _message_cache()
    if not cache.get("class_groups_checked"):
        cache["class_groups_checked"] = 1
        ensure_all_class_message_groups()


def _is_admin_label_role(role):
    if role == "Administrator" or role == "System Manager":
        return True
    if role == "Education Manager":
        return True
    return False


def _is_admin_role(role):
    if _is_admin_label_role(role):
        return True
    if role == "Academics User":
        return True
    return False


def _user_enabled(user):
    if not user or user == "Guest":
        return False
    return bool(frappe.db.get_value("User", user, "enabled"))


def _get_user_roles(user):
    return frappe.get_roles(user)


def _is_message_admin(user=None):
    user = user or frappe.session.user
    if user == "Administrator":
        return True
    roles = _get_user_roles(user)
    i = 0
    while i < len(roles):
        if roles[i] == "System Manager" or roles[i] == "Education Manager":
            return True
        i = i + 1
    return False


def _can_view_poll_voters(user, poll_doc):
    if not user or user == "Guest":
        return False
    if poll_doc.created_by == user:
        return True
    return _is_message_admin(user)


def _get_admin_users():
    cache = _message_cache()
    if "admin_users" in cache:
        return cache["admin_users"]
    users = []
    seen = {}
    if _user_enabled("Administrator"):
        users.append("Administrator")
        seen["Administrator"] = 1
    roles = ["System Manager", "Education Manager", "Academics User"]
    r = 0
    while r < len(roles):
        rows = frappe.get_all(
            "Has Role",
            filters={"role": roles[r], "parenttype": "User"},
            fields=["parent"],
            order_by="parent asc",
        )
        i = 0
        while i < len(rows):
            uname = rows[i].get("parent")
            if uname and uname not in seen and _user_enabled(uname):
                seen[uname] = 1
                users.append(uname)
            i = i + 1
        r = r + 1
    cache["admin_users"] = users
    return users


def _get_instructor_users():
    cache = _message_cache()
    if "instructor_users" in cache:
        return cache["instructor_users"]
    users = []
    seen = {}
    rows = frappe.get_all(
        "Instructor",
        filters={"status": "Active"},
        fields=["name", "instructor_name", "custom_instructor_email"],
        order_by="name asc",
    )
    i = 0
    while i < len(rows):
        email = str(rows[i].get("custom_instructor_email") or "").strip()
        if email and email not in seen and _user_enabled(email):
            seen[email] = 1
            users.append(email)
        i = i + 1
    cache["instructor_users"] = users
    return users


def _get_student_users():
    cache = _message_cache()
    if "student_users" in cache:
        return cache["student_users"]
    users = []
    seen = {}
    rows = frappe.get_all(
        "Student",
        filters={"enabled": 1},
        fields=["name", "student_name", "user"],
        order_by="name asc",
    )
    i = 0
    while i < len(rows):
        email = str(rows[i].get("user") or "").strip()
        if email and email not in seen and _user_enabled(email):
            seen[email] = 1
            users.append(email)
        i = i + 1
    cache["student_users"] = users
    return users


def _has_enabled_instructor_record(user):
    cache = _message_cache()
    key = "has_instructor_record:" + str(user)
    if key in cache:
        return cache[key]
    rows = frappe.get_all(
        "Instructor",
        filters={"custom_instructor_email": user, "status": "Active"},
        pluck="name",
        limit_page_length=1,
    )
    cache[key] = bool(rows)
    return cache[key]


def _has_enabled_student_record(user):
    cache = _message_cache()
    key = "has_student_record:" + str(user)
    if key in cache:
        return cache[key]
    rows = frappe.get_all(
        "Student",
        filters={"user": user, "enabled": 1},
        pluck="name",
        limit_page_length=1,
    )
    cache[key] = bool(rows)
    return cache[key]


def _get_role_label(user):
    cache = _message_cache()
    key = "role_label:" + str(user)
    if key in cache:
        return cache[key]
    label = ""
    if user == "Administrator":
        label = "Admin"
    elif _has_enabled_instructor_record(user):
        label = "Instructor"
    elif _has_enabled_student_record(user):
        label = "Student"
    else:
        roles = _get_user_roles(user)
        i = 0
        while i < len(roles):
            if _is_admin_label_role(roles[i]):
                label = "Admin"
                break
            i = i + 1
    cache[key] = label
    return label


def is_staff_message_role(user=None):
    user = user or frappe.session.user
    roles = _get_user_roles(user)
    i = 0
    while i < len(roles):
        if _is_admin_role(roles[i]):
            return True
        i = i + 1
    return False


def is_instructor_message_role(user=None):
    user = user or frappe.session.user
    return user in _get_instructor_users()


def _can_create_message_group(user=None):
    user = user or frappe.session.user
    if _is_message_admin(user):
        return True
    return is_instructor_message_role(user)


def _can_manage_message_candidates(user=None):
    user = user or frappe.session.user
    if _is_message_admin(user):
        return True
    return is_instructor_message_role(user)


def _can_use_messages(user):
    if is_staff_message_role(user):
        return True
    if user in _get_instructor_users():
        return True
    if user in _get_student_users():
        return True
    groups = frappe.get_all("Message Group", filters={}, pluck="name")
    i = 0
    while i < len(groups):
        if is_group_member(user, groups[i]):
            return True
        i = i + 1
    return False


def _user_display_name(user):
    return get_fullname(user) or user


def get_staff_group_members():
    members = []
    seen = {}
    groups = [_get_admin_users(), _get_instructor_users()]
    g = 0
    while g < len(groups):
        i = 0
        while i < len(groups[g]):
            uname = groups[g][i]
            if uname not in seen:
                seen[uname] = 1
                members.append(uname)
            i = i + 1
        g = g + 1
    return members


def get_all_members_group_members():
    members = []
    seen = {}
    groups = [_get_admin_users(), _get_instructor_users(), _get_student_users()]
    g = 0
    while g < len(groups):
        i = 0
        while i < len(groups[g]):
            uname = groups[g][i]
            if uname not in seen:
                seen[uname] = 1
                members.append(uname)
            i = i + 1
        g = g + 1
    return members


def get_staff_group_member_details():
    details = []
    users = get_staff_group_members()
    i = 0
    while i < len(users):
        details.append({
            "user": users[i],
            "display_name": _user_display_name(users[i]),
            "role_label": _get_role_label(users[i]),
        })
        i = i + 1
    return details


def _get_removed_users(group):
    cache = _message_cache()
    key = "removed:" + str(group)
    if key in cache:
        return cache[key]
    rows = frappe.get_all(
        REMOVED_MEMBER_DOCTYPE,
        filters={"group": group},
        fields=["user"],
        order_by="name asc",
    )
    users = {}
    i = 0
    while i < len(rows):
        users[rows[i].get("user")] = 1
        i = i + 1
    cache[key] = users
    return users


def _group_doc(group, fields=None):
    if not group:
        return None
    if fields is None:
        fields = ["name", "group_name", "group_type", "student_group", "owner", "description", "is_default", "send_mode"]
    return frappe.db.get_value("Message Group", group, fields, as_dict=True)


def _get_custom_group_members(group):
    cache = _message_cache()
    key = "custom_members:" + str(group)
    if key in cache:
        return cache[key]
    rows = frappe.get_all(
        MESSAGE_GROUP_MEMBER_DOCTYPE,
        filters={"parent": group, "parenttype": "Message Group", "parentfield": "members"},
        fields=["name", "user", "is_admin", "added_by", "added_on"],
        order_by="name asc",
    )
    result = []
    seen = {}
    i = 0
    while i < len(rows):
        user = rows[i].get("user")
        if user and user not in seen:
            seen[user] = 1
            result.append(rows[i])
        i = i + 1
    cache[key] = result
    return result


def _get_explicit_group_member_name(group, user):
    return frappe.db.get_value(
        MESSAGE_GROUP_MEMBER_DOCTYPE,
        {"parent": group, "parenttype": "Message Group", "parentfield": "members", "user": user},
        "name",
    )


def _filter_enabled_users(members):
    result = []
    i = 0
    while i < len(members):
        user = members[i]
        if _user_enabled(user) and user not in result:
            result.append(user)
        i = i + 1
    return result


def _append_custom_group_member(doc, user, actor, is_admin=0):
    doc.append("members", {
        "doctype": MESSAGE_GROUP_MEMBER_DOCTYPE,
        "user": user,
        "is_admin": 1 if is_admin else 0,
        "added_by": actor,
        "added_on": now_datetime(),
    })


def _remove_custom_group_member(group, user):
    frappe.db.delete(
        MESSAGE_GROUP_MEMBER_DOCTYPE,
        {"parent": group, "parenttype": "Message Group", "parentfield": "members", "user": user},
    )


def _delete_removed_member(group, user):
    name = frappe.db.get_value(REMOVED_MEMBER_DOCTYPE, {"group": group, "user": user}, "name")
    if name:
        frappe.delete_doc(REMOVED_MEMBER_DOCTYPE, name, ignore_permissions=True, force=True, delete_permanently=True)


def _upsert_removed_member(group, user, removed_by, reason=None):
    values = {"removed_by": removed_by, "removed_on": now_datetime()}
    if reason is not None:
        values["reason"] = reason
    existing = frappe.db.get_value(REMOVED_MEMBER_DOCTYPE, {"group": group, "user": user}, "name")
    if existing:
        frappe.db.set_value(REMOVED_MEMBER_DOCTYPE, existing, values)
        return
    row = {
        "doctype": REMOVED_MEMBER_DOCTYPE,
        "group": group,
        "user": user,
    }
    row.update(values)
    frappe.get_doc(row).insert(ignore_permissions=True)


def _can_manage_custom_group(user, group_doc=None, group=None):
    if not user or user == "Guest":
        return False
    group_doc = group_doc or _group_doc(group)
    if not group_doc or group_doc.group_type != CUSTOM_GROUP_TYPE:
        return False
    if not is_group_member(user, group_doc.name):
        return False
    if group_doc.owner == user:
        return True
    if _is_message_admin(user) or is_instructor_message_role(user):
        return True
    member = frappe.db.get_value(
        MESSAGE_GROUP_MEMBER_DOCTYPE,
        {"parent": group_doc.name, "parenttype": "Message Group", "parentfield": "members", "user": user},
        "is_admin",
    )
    return bool(member)


def _can_manage_members(user, group_doc):
    if group_doc.group_type == CUSTOM_GROUP_TYPE:
        return _can_manage_custom_group(user, group_doc)
    return _is_message_admin(user)


def _can_delete_custom_group(user, group_doc):
    if not user or user == "Guest" or group_doc.group_type != CUSTOM_GROUP_TYPE:
        return False
    return bool(group_doc.owner == user or _is_message_admin(user))


def _require_custom_group(group):
    user = _require_logged_in_user()
    if not group:
        frappe.throw("Group is required.")
    group_doc = _group_doc(group)
    if not group_doc:
        frappe.throw("Group not found.", frappe.DoesNotExistError)
    if group_doc.group_type != CUSTOM_GROUP_TYPE:
        frappe.throw("Only custom groups support this action.", frappe.PermissionError)
    return user, group_doc


def _require_group_membership(group, action):
    user = _require_logged_in_user()
    if not group:
        frappe.throw("Group is required.")
    group_doc = _group_doc(group)
    if not group_doc:
        frappe.throw("Group not found.", frappe.DoesNotExistError)
    if not is_group_member(user, group_doc.name):
        frappe.throw("You do not have permission to " + action + " in this group.", frappe.PermissionError)
    return user, group_doc


def _publish_group_access_changed(group, member, removed):
    frappe.publish_realtime(
        event="messages_group_access_changed",
        message={"group": group, "user": member, "removed": removed},
        user=member,
        after_commit=True,
    )


def _publish_group_list_changed(group, members):
    i = 0
    while i < len(members):
        frappe.publish_realtime(
            event="messages_group_list_changed",
            message={"group": group},
            user=members[i],
            after_commit=True,
        )
        i = i + 1


def _publish_group_deleted(group, group_name, members):
    i = 0
    while i < len(members):
        frappe.publish_realtime(
            event="messages_group_deleted",
            message={"group": group, "group_name": group_name},
            user=members[i],
            after_commit=True,
        )
        i = i + 1


def _class_group_student_group(group_doc):
    if not group_doc or group_doc.get("group_type") != "Class":
        return None
    return group_doc.get("student_group") or None


def _class_group_disabled(group_doc):
    student_group = _class_group_student_group(group_doc)
    if not student_group or not frappe.db.exists("Student Group", student_group):
        return True
    return bool(frappe.db.get_value("Student Group", student_group, "disabled"))


def _user_from_student(student):
    row = frappe.db.get_value("Student", student, ["enabled", "user"], as_dict=True)
    if not row or not row.enabled:
        return None
    user = str(row.user or "").strip()
    if not _user_enabled(user):
        return None
    return user


def _user_from_instructor(instructor):
    row = frappe.db.get_value(
        "Instructor",
        instructor,
        ["status", "custom_instructor_email"],
        as_dict=True,
    )
    if not row or row.status != "Active":
        return None
    email = str(row.custom_instructor_email or "").strip()
    if not _user_enabled(email):
        return None
    return email


def _get_class_group_member_users(group_doc):
    members = []
    seen = {}
    student_group = _class_group_student_group(group_doc)
    if not student_group or _class_group_disabled(group_doc):
        return members
    students = frappe.get_all(
        "Student Group Student",
        filters={"parent": student_group, "active": 1},
        pluck="student",
        order_by="name asc",
    )
    instructors = frappe.get_all(
        "Student Group Instructor",
        filters={"parent": student_group},
        pluck="instructor",
        order_by="name asc",
    )
    groups = [
        [_user_from_student(student) for student in students],
        [_user_from_instructor(instructor) for instructor in instructors],
    ]
    g = 0
    while g < len(groups):
        i = 0
        while i < len(groups[g]):
            user = groups[g][i]
            if user and user not in seen:
                seen[user] = 1
                members.append(user)
            i = i + 1
        g = g + 1
    return members


def _get_group_member_users(group):
    group_doc = frappe.db.get_value(
        "Message Group",
        group,
        ["group_type", "student_group"],
        as_dict=True,
    )
    if not group_doc:
        return []
    if group_doc.group_type == "Class":
        members = _get_class_group_member_users(group_doc)
    elif group_doc.group_type == "Staff":
        members = get_staff_group_members()
    elif group_doc.group_type == "All":
        members = get_all_members_group_members()
    elif group_doc.group_type == CUSTOM_GROUP_TYPE:
        members = [row.get("user") for row in _get_custom_group_members(group) if row.get("user")]
    else:
        return []
    removed = _get_removed_users(group)
    result = []
    i = 0
    while i < len(members):
        if members[i] not in removed:
            result.append(members[i])
        i = i + 1
    return result


def _get_member_rule_map(group):
    cache = _message_cache()
    key = "send_rules:" + str(group)
    if key in cache:
        return cache[key]
    rows = frappe.get_all(
        MESSAGE_GROUP_MEMBER_DOCTYPE,
        filters={"parent": group, "parenttype": "Message Group", "parentfield": "members"},
        fields=["user", "read_only", "can_send"],
        order_by="name asc",
    )
    result = {}
    i = 0
    while i < len(rows):
        user = rows[i].get("user")
        if user and user not in result:
            result[user] = {
                "read_only": cint(rows[i].get("read_only")),
                "can_send": cint(rows[i].get("can_send")),
            }
        i = i + 1
    cache[key] = result
    return result


def _get_member_rule_row(group, user):
    return _get_member_rule_map(group).get(user)


def _get_group_member_details(group):
    details = []
    users = _get_group_member_users(group)
    rules = _get_member_rule_map(group)
    i = 0
    while i < len(users):
        row = rules.get(users[i]) or {}
        details.append({
            "user": users[i],
            "display_name": _user_display_name(users[i]),
            "role_label": _get_role_label(users[i]),
            "read_only": bool(row.get("read_only")),
            "can_send": bool(row.get("can_send")),
        })
        i = i + 1
    return details


def is_group_member(user, group_name):
    user = user or frappe.session.user
    if user == "Guest":
        return False
    group_doc = frappe.db.get_value(
        "Message Group",
        group_name,
        ["group_type", "student_group"],
        as_dict=True,
    )
    if not group_doc:
        return False
    if group_doc.group_type == "Class" and _class_group_disabled(group_doc):
        return False
    if user in _get_removed_users(group_name):
        return False
    members = _get_group_member_users(group_name)
    i = 0
    while i < len(members):
        if members[i] == user:
            return True
        i = i + 1
    return False


def _is_builtin_group(group_doc):
    if not group_doc:
        return False
    name = str(group_doc.get("group_name") or group_doc.get("name") or "")
    doc_name = str(group_doc.get("name") or "")
    if name in (DEFAULT_STAFF_GROUP_NAME, DEFAULT_ALL_GROUP_NAME):
        return True
    if doc_name in (DEFAULT_STAFF_GROUP_NAME, DEFAULT_ALL_GROUP_NAME):
        return True
    return False


def _can_user_send_in_group(user, group):
    label = _get_role_label(user)
    row = _get_member_rule_row(group, user) or {}
    if row.get("read_only"):
        return False, "You can only read this chat"
    if label == "Admin" or label == "Instructor":
        return True, ""
    mode = frappe.db.get_value("Message Group", group, "send_mode") or SEND_MODE_EVERYONE
    mode = str(mode)
    if mode == SEND_MODE_ADMINS_INSTRUCTORS:
        return False, "Only admins and instructors can send messages"
    if mode == SEND_MODE_SELECTED:
        if row.get("can_send"):
            return True, ""
        return False, "You can only read this chat"
    return True, ""


def _can_manage_send_rules(user, group_doc):
    label = _get_role_label(user)
    if label != "Admin" and label != "Instructor":
        return False
    if label == "Admin":
        return True
    if not group_doc or _is_builtin_group(group_doc):
        return False
    return is_group_member(user, group_doc.get("name"))


def _require_send_rule_manager(group, action):
    user = _require_logged_in_user()
    label = _get_role_label(user)
    if label != "Admin" and label != "Instructor":
        frappe.throw("Students cannot " + action + ".", frappe.PermissionError)
    group_doc = _group_doc(group)
    if not group_doc:
        frappe.throw("Group not found.", frappe.DoesNotExistError)
    if not _can_manage_send_rules(user, group_doc):
        if _is_builtin_group(group_doc):
            frappe.throw("Only administrators can " + action + " for this group.", frappe.PermissionError)
        frappe.throw("You do not have permission to " + action + " in this group.", frappe.PermissionError)
    return user, group_doc


def _ensure_member_rule_row(group, user, actor):
    name = _get_explicit_group_member_name(group, user)
    if name:
        return name
    doc = frappe.get_doc("Message Group", group)
    _append_custom_group_member(doc, user, actor, is_admin=0)
    doc.save(ignore_permissions=True)
    _clear_message_cache()
    return _get_explicit_group_member_name(group, user)


def _audit_send_rule_change(changed_by, group, member, setting, old_value, new_value):
    frappe.get_doc({
        "doctype": MESSAGE_SEND_RULE_AUDIT_DOCTYPE,
        "changed_by": changed_by,
        "group": group,
        "member": member or None,
        "setting": setting,
        "old_value": str(old_value),
        "new_value": str(new_value),
        "changed_on": now_datetime(),
    }).insert(ignore_permissions=True)


def _publish_group_send_rules_changed(group, actor):
    payload = {"group": group}
    members = _get_group_member_users(group)
    i = 0
    while i < len(members):
        if members[i] != actor:
            frappe.publish_realtime(
                event="messages_group_send_rules_changed",
                message=payload,
                user=members[i],
                after_commit=True,
            )
        i = i + 1


def ensure_default_staff_group():
    existing = frappe.db.exists("Message Group", DEFAULT_STAFF_GROUP_NAME)
    if not existing:
        existing = frappe.db.get_value("Message Group", {"group_name": DEFAULT_STAFF_GROUP_NAME}, "name")
    if existing:
        return existing
    doc = frappe.get_doc({
        "doctype": "Message Group",
        "group_name": DEFAULT_STAFF_GROUP_NAME,
        "group_type": "Staff",
        "is_default": 1,
        "description": "Default group for Administrators and all Instructors"
    })
    doc.insert(ignore_permissions=True)
    return doc.name


def ensure_all_members_group():
    existing = frappe.db.exists("Message Group", DEFAULT_ALL_GROUP_NAME)
    if not existing:
        existing = frappe.db.get_value("Message Group", {"group_name": DEFAULT_ALL_GROUP_NAME}, "name")
    if existing:
        if frappe.db.get_value("Message Group", existing, "group_type") != "All":
            frappe.db.set_value("Message Group", existing, "group_type", "All")
        return existing
    doc = frappe.get_doc({
        "doctype": "Message Group",
        "group_name": DEFAULT_ALL_GROUP_NAME,
        "group_type": "All",
        "is_default": 0,
        "description": "All Administrators, Instructors and Students"
    })
    doc.insert(ignore_permissions=True)
    return doc.name


def ensure_class_message_group(student_group_name):
    if not student_group_name or not frappe.db.exists("Student Group", student_group_name):
        return None
    by_link = frappe.db.get_value("Message Group", {"student_group": student_group_name}, "name")
    by_name = frappe.db.get_value("Message Group", {"group_name": student_group_name}, "name")
    target = by_link or by_name
    description = "Class group for " + student_group_name
    if target:
        current = frappe.db.get_value(
            "Message Group",
            target,
            ["group_name", "group_type", "student_group", "description"],
            as_dict=True,
        )
        values = {}
        if current.group_type != "Class":
            values["group_type"] = "Class"
        if current.student_group != student_group_name:
            values["student_group"] = student_group_name
        if current.group_name != student_group_name:
            values["group_name"] = student_group_name
        if (current.description or "") != description:
            values["description"] = description
        if values:
            frappe.db.set_value("Message Group", target, values, update_modified=False)
        if by_link and by_name and by_link != by_name and by_name != target:
            frappe.db.set_value("Message Group", by_name, "student_group", None, update_modified=False)
        return target
    doc = frappe.get_doc({
        "doctype": "Message Group",
        "group_name": student_group_name,
        "group_type": "Class",
        "student_group": student_group_name,
        "is_default": 0,
        "description": description,
    })
    doc.insert(ignore_permissions=True)
    return doc.name


def ensure_all_class_message_groups():
    names = frappe.get_all("Student Group", filters={}, pluck="name", order_by="name asc")
    i = 0
    while i < len(names):
        ensure_class_message_group(names[i])
        i = i + 1
    return names


def student_group_after_insert(doc, method=None):
    ensure_class_message_group(doc.name)


def student_group_on_update(doc, method=None):
    ensure_class_message_group(doc.name)


def student_group_after_rename(doc, method=None, old=None, new=None, merge=None):
    old_name = old or doc.name
    new_name = new or doc.name
    target = frappe.db.get_value("Message Group", {"student_group": old_name}, "name")
    if not target:
        target = frappe.db.get_value("Message Group", {"group_name": old_name}, "name")
    if target and target == old_name and target != new_name and not frappe.db.exists("Message Group", new_name):
        frappe.rename_doc("Message Group", target, new_name)
    ensure_class_message_group(new_name)


def student_group_on_trash(doc, method=None):
    names = frappe.get_all("Message Group", filters={"student_group": doc.name}, pluck="name")
    names = names + frappe.get_all("Message Group", filters={"group_name": doc.name}, pluck="name")
    unique_names = []
    i = 0
    while i < len(names):
        if names[i] and names[i] not in unique_names:
            unique_names.append(names[i])
        i = i + 1
    i = 0
    while i < len(unique_names):
        if frappe.db.exists("Message Group", unique_names[i]):
            frappe.delete_doc("Message Group", unique_names[i], ignore_permissions=True)
        i = i + 1


def _plain_preview(text):
    clean = HTML_TAG_PATTERN.sub(" ", str(text or ""))
    clean = clean.replace("\u0000", "")
    preview = " ".join(clean.strip().split())
    if len(preview) > 80:
        preview = preview[:77] + "..."
    return preview


def _message_preview_text(message):
    if isinstance(message, str):
        return _plain_preview(message)
    data = message or {}
    msg_type = str(data.get("message_type") or "text")
    caption = _plain_preview(data.get("message_text"))
    if msg_type == "sticker":
        return "Sticker"
    if caption:
        return caption
    label = "New message"
    if msg_type == "image":
        label = "Photo"
    elif msg_type == "video":
        label = "Video"
    elif msg_type == "audio":
        label = "Voice message" if data.get("is_voice_note") else "Audio"
    elif msg_type == "file":
        file_name = data.get("file_name") or ""
        attachment = data.get("attachment")
        if not file_name and isinstance(attachment, dict):
            file_name = attachment.get("file_name") or ""
        label = "Document: " + str(file_name)
    elif msg_type == "contact":
        label = "Contact"
    elif msg_type == "poll":
        poll_data = data.get("poll")
        question = data.get("poll_question") or ""
        if not question and isinstance(poll_data, dict):
            question = poll_data.get("question") or ""
        if not question and poll_data and not isinstance(poll_data, dict) and frappe.db.exists("Group Poll", poll_data):
            question = frappe.db.get_value("Group Poll", poll_data, "question")
        label = "Poll: " + str(question or "")
    return _plain_preview(label)


def _message_preview(message):
    return _message_preview_text(message)


def _serialize_poll(poll_name, user=None):
    poll_doc = frappe.get_doc("Group Poll", poll_name)
    options = []
    voters_by_option = {}
    can_view_voters = _can_view_poll_voters(user, poll_doc)
    option_rows = poll_doc.get("options") or []
    i = 0
    while i < len(option_rows):
        row = option_rows[i]
        option = {
            "index": int(row.option_index or i + 1),
            "text": str(row.option_text or ""),
            "count": 0,
        }
        if can_view_voters:
            option["voters"] = []
        options.append(option)
        i = i + 1
    votes = frappe.get_all(
        "Group Poll Vote",
        filters={"poll": poll_name},
        fields=["user", "option_index"],
    )
    counts = {}
    my_vote = 0
    i = 0
    while i < len(votes):
        option_index = int(votes[i].option_index or 0)
        counts[option_index] = counts.get(option_index, 0) + 1
        if user and votes[i].user == user:
            my_vote = option_index
        if can_view_voters:
            voters = voters_by_option.setdefault(option_index, [])
            display_name = _user_display_name(votes[i].user)
            if display_name not in voters:
                voters.append(display_name)
        i = i + 1
    i = 0
    while i < len(options):
        index = options[i]["index"]
        options[i]["count"] = counts.get(index, 0)
        if can_view_voters:
            options[i]["voters"] = voters_by_option.get(index, [])
        i = i + 1
    return {
        "name": poll_name,
        "group": poll_doc.group,
        "question": poll_doc.question or "",
        "is_closed": bool(poll_doc.is_closed),
        "can_close": bool(user and poll_doc.created_by == user),
        "can_view_voters": can_view_voters,
        "options": options,
        "total_votes": len(votes),
        "my_vote": my_vote,
    }


def _serialize_message(row, user=None):
    as_dict = getattr(row, "as_dict", None)
    if callable(as_dict):
        data = as_dict()
    elif isinstance(row, dict):
        data = dict(row)
    else:
        data = dict(getattr(row, "__dict__", {}) or {})
    msg_type = str(data.get("message_type") or "text")
    if msg_type not in MESSAGE_TYPES:
        msg_type = "text"
    item = {
        "name": data.get("name") or "",
        "sender": data.get("sender") or "",
        "sender_name": _user_display_name(data.get("sender")),
        "role_label": _get_role_label(data.get("sender")),
        "message_type": msg_type,
        "message_text": data.get("message_text") or "",
        "sent_at": str(data.get("sent_at") or data.get("creation") or ""),
        "creation": str(data.get("creation") or ""),
        "is_own": bool(user and data.get("sender") == user),
        "is_voice_note": bool(data.get("is_voice_note")),
        "attachment": None,
        "contact": None,
        "poll": None,
    }
    if data.get("attachment_file"):
        encoded = quote(str(data.get("name") or ""), safe="")
        item["attachment"] = {
            "message_name": data.get("name") or "",
            "file_name": data.get("file_name") or "",
            "file_size": int(data.get("file_size") or 0),
            "mime_type": data.get("mime_type") or "application/octet-stream",
            "download_url": "/api/method/backend_custom_app.api.messages.download_group_file?message_name=" + encoded,
            "preview_url": "/api/method/backend_custom_app.api.messages.download_group_file?message_name=" + encoded + "&inline=1",
            "inline": msg_type in ("image", "video", "audio"),
        }
    if data.get("contact_user"):
        item["contact"] = {
            "user": data.get("contact_user"),
            "display_name": _user_display_name(data.get("contact_user")),
            "role_label": _get_role_label(data.get("contact_user")),
        }
    if data.get("poll") and frappe.db.exists("Group Poll", data.get("poll")):
        item["poll"] = _serialize_poll(data.get("poll"), user)
    return item


def _publish_new_message(payload, group, sender):
    members = _get_group_member_users(group)
    event_payload = dict(payload)
    event_payload["group"] = group
    i = 0
    while i < len(members):
        if members[i] != sender:
            frappe.publish_realtime(event="group_message_new", message=event_payload, user=members[i])
        i = i + 1
    _notify_group_members(sender, _user_display_name(sender), group, payload)


def _delete_message_related(doc):
    file_name = doc.get("attachment_file")
    if file_name and frappe.db.exists("File", file_name):
        frappe.delete_doc("File", file_name, force=True, ignore_permissions=True, delete_permanently=True)
    poll_name = doc.get("poll")
    if poll_name and frappe.db.exists("Group Poll", poll_name):
        votes = frappe.get_all("Group Poll Vote", filters={"poll": poll_name}, pluck="name")
        i = 0
        while i < len(votes):
            frappe.delete_doc("Group Poll Vote", votes[i], force=True, ignore_permissions=True, delete_permanently=True)
            i = i + 1
        frappe.delete_doc("Group Poll", poll_name, force=True, ignore_permissions=True, delete_permanently=True)


def _upsert_group_message_read(user, group, field, value):
    existing = frappe.db.get_value("Group Message Read", {"user": user, "group": group}, "name")
    if existing:
        frappe.db.set_value("Group Message Read", existing, field, value)
        return
    row = {
        "doctype": "Group Message Read",
        "user": user,
        "group": group,
    }
    row[field] = value
    frappe.get_doc(row).insert(ignore_permissions=True)


def _get_message_read_value(user, group, field):
    return frappe.db.get_value("Group Message Read", {"user": user, "group": group}, field)


def _get_cleared_at(user, group):
    return _get_message_read_value(user, group, "cleared_at")


def _mark_sender_message_read(user, group, stamp):
    if not stamp:
        return
    _upsert_group_message_read(user, group, "last_read_on", str(stamp))


def _later_datetime(first, second):
    left = str(first or "")
    right = str(second or "")
    if left > right:
        return left
    return right


def _effective_message_cutoff(user, group):
    row = frappe.db.get_value(
        "Group Message Read",
        {"user": user, "group": group},
        ["last_read_on", "cleared_at"],
        as_dict=True,
    )
    if not row:
        return None
    return _later_datetime(row.last_read_on, row.cleared_at)


def _last_group_message(group, user=None):
    filters = [["group", "=", group]]
    if user:
        cleared_at = _get_cleared_at(user, group)
        if cleared_at:
            filters.append(["creation", ">", cleared_at])
    rows = frappe.get_all(
        "Group Message",
        filters=filters,
        fields=[
            "message_text",
            "message_type",
            "file_name",
            "poll",
            "poll_question",
            "is_voice_note",
            "sent_at",
            "creation",
        ],
        order_by="creation desc",
        limit_page_length=1,
    )
    if not rows:
        return "", ""
    row = rows[0]
    return _message_preview_text(row), str(row.sent_at or row.creation or "")


@frappe.whitelist()
def get_my_groups():
    user = frappe.session.user
    if user == "Guest":
        frappe.throw("Please login.", frappe.PermissionError)
    _ensure_class_groups_for_request()
    ensure_default_staff_group()
    ensure_all_members_group()
    if not _can_use_messages(user):
        frappe.throw("You are not a member of any group yet.", frappe.PermissionError)
    groups = frappe.get_all(
        "Message Group",
        filters={},
        fields=["name", "group_name", "group_type", "is_default", "description", "owner", "send_mode"],
        order_by="is_default desc, name asc",
    )
    with_messages = []
    without_messages = []
    i = 0
    while i < len(groups):
        g = groups[i]
        if is_group_member(user, g.name):
            members = _get_group_member_details(g.name)
            preview, last_at = _last_group_message(g.name, user)
            can_send, send_block_reason = _can_user_send_in_group(user, g.name)
            own_rule = _get_member_rule_row(g.name, user) or {}
            item = {
                "name": g.name,
                "group_name": g.group_name,
                "group_type": g.group_type,
                "is_default": g.is_default,
                "description": g.description or "",
                "members_count": len(members),
                "members": members,
                "unread_count": get_unread_count_for_group(user, g.name),
                "can_remove_members": _is_message_admin(user),
                "is_custom": bool(g.group_type == CUSTOM_GROUP_TYPE),
                "created_by": g.owner or "",
                "can_manage_members": _can_manage_members(user, g),
                "can_delete_group": _can_delete_custom_group(user, g),
                "can_create_group": _can_create_message_group(user),
                "can_exit_group": True,
                "send_mode": str(g.get("send_mode") or SEND_MODE_EVERYONE),
                "current_user_role_label": _get_role_label(user),
                "current_user_can_send": can_send,
                "current_user_read_only": bool(own_rule.get("read_only")),
                "current_user_send_block_reason": send_block_reason,
                "can_manage_send_rules": _can_manage_send_rules(user, g),
                "current_user": user,
                "last_message_text": preview,
                "last_message_at": last_at,
                "last_message_sent_at": last_at,
                "archived": bool(frappe.db.get_value("Group Message Read", {"user": user, "group": g.name}, "archived")),
            }
            if last_at:
                with_messages.append(item)
            else:
                without_messages.append(item)
        i = i + 1
    with_messages.sort(key=lambda row: (row["last_message_at"], row["name"]), reverse=True)
    return with_messages + without_messages


def get_unread_count_for_group(user, group_name):
    cutoff = _effective_message_cutoff(user, group_name)
    filters = [["group", "=", group_name], ["sender", "!=", user]]
    if cutoff:
        filters.append(["creation", ">", cutoff])
    return frappe.db.count("Group Message", filters)


@frappe.whitelist()
def get_unread_counts():
    user = frappe.session.user
    if user == "Guest":
        frappe.throw("Please login.", frappe.PermissionError)
    _ensure_class_groups_for_request()
    ensure_default_staff_group()
    ensure_all_members_group()
    if not _can_use_messages(user):
        return {"unread_count": 0}
    groups = frappe.get_all("Message Group", filters={}, fields=["name", "group_type"])
    total = 0
    i = 0
    while i < len(groups):
        g = groups[i]
        if is_group_member(user, g.name):
            total = total + get_unread_count_for_group(user, g.name)
        i = i + 1
    return {"unread_count": total}


@frappe.whitelist()
def get_group_messages(group, limit=50, before=None):
    user = frappe.session.user
    if user == "Guest":
        frappe.throw("Please login.", frappe.PermissionError)
    _ensure_class_groups_for_request()
    ensure_default_staff_group()
    ensure_all_members_group()
    if not group:
        frappe.throw("Group is required.")
    if not frappe.db.exists("Message Group", group):
        frappe.throw("Group not found.", frappe.DoesNotExistError)
    if not is_group_member(user, group):
        frappe.throw("You do not have permission to view this group.", frappe.PermissionError)
    lim = 50
    try:
        lim = int(limit)
    except Exception:
        lim = 50
    if lim < 1:
        lim = 1
    if lim > 100:
        lim = 100
    filters = [["group", "=", group]]
    cleared_at = _get_cleared_at(user, group)
    if cleared_at:
        filters.append(["creation", ">", cleared_at])
    if before:
        filters.append(["creation", "<", before])
    rows = frappe.get_all(
        "Group Message",
        filters=filters,
        fields=[
            "name",
            "sender",
            "message_text",
            "sent_at",
            "creation",
            "owner",
            "message_type",
            "attachment_file",
            "file_name",
            "file_size",
            "mime_type",
            "contact_user",
            "poll",
            "poll_question",
            "is_voice_note",
        ],
        order_by="creation desc",
        limit_page_length=lim
    )
    messages = []
    i = 0
    while i < len(rows):
        messages.append(_serialize_message(rows[i], user))
        i = i + 1
    messages.reverse()
    return messages


@frappe.whitelist()
def get_group_send_rules(group):
    user, group_doc = _require_send_rule_manager(group, "view send rules")
    return {
        "group": group_doc.name,
        "send_mode": str(group_doc.get("send_mode") or SEND_MODE_EVERYONE),
        "can_manage": True,
        "members": _get_group_member_details(group_doc.name),
    }


@frappe.whitelist()
def update_group_send_rules(group, send_mode=None, members=None):
    user, group_doc = _require_send_rule_manager(group, "change send rules")
    mode = str(send_mode or "")
    if mode not in SEND_MODES:
        frappe.throw("Unsupported send mode.")
    old_mode = str(group_doc.get("send_mode") or SEND_MODE_EVERYONE)
    if old_mode != mode:
        _audit_send_rule_change(user, group_doc.name, None, "send_mode", old_mode, mode)
        frappe.db.set_value("Message Group", group_doc.name, "send_mode", mode, update_modified=False)
    if mode == SEND_MODE_SELECTED:
        requested = _parse_member_list(members if members is not None else [])
        group_members = _get_group_member_users(group_doc.name)
        chosen = {}
        i = 0
        while i < len(requested):
            if requested[i] in group_members:
                chosen[requested[i]] = 1
            i = i + 1
        i = 0
        while i < len(group_members):
            member = group_members[i]
            row = _get_member_rule_row(group_doc.name, member) or {}
            old_value = cint(row.get("can_send"))
            new_value = 1 if chosen.get(member) else 0
            if old_value != new_value:
                row_name = _ensure_member_rule_row(group_doc.name, member, user)
                frappe.db.set_value(MESSAGE_GROUP_MEMBER_DOCTYPE, row_name, "can_send", new_value, update_modified=False)
                _audit_send_rule_change(user, group_doc.name, member, "can_send", old_value, new_value)
            i = i + 1
    _clear_message_cache()
    frappe.db.commit()
    _publish_group_send_rules_changed(group_doc.name, user)
    return get_group_send_rules(group_doc.name)


@frappe.whitelist()
def set_group_member_send_restriction(group, member=None, read_only=0):
    user, group_doc = _require_send_rule_manager(group, "change member send access")
    target = str(member or "").strip()
    if not target:
        frappe.throw("Member is required.")
    if target == user:
        frappe.throw("You cannot change your own send access.", frappe.PermissionError)
    if target == "Administrator":
        frappe.throw("Administrator cannot be restricted.", frappe.PermissionError)
    if not is_group_member(target, group_doc.name):
        frappe.throw("That member is not in this group.", frappe.PermissionError)
    viewer_label = _get_role_label(user)
    target_label = _get_role_label(target)
    if viewer_label == "Instructor" and target_label != "Student":
        frappe.throw("Instructors can restrict students only.", frappe.PermissionError)
    value = str(read_only).lower()
    new_value = 1 if value in ("1", "true", "yes") else 0
    row = _get_member_rule_row(group_doc.name, target) or {}
    old_value = cint(row.get("read_only"))
    if old_value != new_value:
        row_name = _ensure_member_rule_row(group_doc.name, target, user)
        frappe.db.set_value(MESSAGE_GROUP_MEMBER_DOCTYPE, row_name, "read_only", new_value, update_modified=False)
        _audit_send_rule_change(user, group_doc.name, target, "read_only", old_value, new_value)
    _clear_message_cache()
    frappe.db.commit()
    _publish_group_send_rules_changed(group_doc.name, user)
    return get_group_send_rules(group_doc.name)


@frappe.whitelist()
def publish_group_typing(group):
    user, group_doc = _require_group_membership(group, "send typing")
    key = "messages_group_typing:" + str(group_doc.name) + ":" + str(user)
    cache = frappe.cache()
    last = cache.get_value(key)
    current = now_datetime().timestamp()
    if last:
        try:
            if current - float(last) < TYPING_RATE_LIMIT_SECONDS:
                return {"ok": True, "throttled": True}
        except Exception:
            pass
    cache.set_value(key, current, expires_in_sec=TYPING_RATE_LIMIT_SECONDS)
    payload = {
        "group": group_doc.name,
        "user": user,
        "sender_name": _user_display_name(user),
        "typing": True,
    }
    members = _get_group_member_users(group_doc.name)
    i = 0
    while i < len(members):
        if members[i] != user:
            frappe.publish_realtime(event="messages_group_typing", message=payload, user=members[i])
        i = i + 1
    return {"ok": True}


def _message_notification_marker(group):
    return "[messages:" + str(group) + "]"


def _message_marker_groups(description):
    return MESSAGE_MARKER_PATTERN.findall(description or "")


def message_group_from_notification(description=None, link=None):
    groups = _message_marker_groups(description)
    if groups:
        return groups[0]
    if link and "?" in link:
        values = parse_qs(link.split("?", 1)[1])
        if values.get("group"):
            return values["group"][0]
    return ""


def _group_label(group):
    return frappe.db.get_value("Message Group", group, "group_name") or group


def _notification_link_for(user, group):
    encoded = quote(str(group), safe="")
    if _get_role_label(user) == "Student":
        return "/student-portal#/messages?group=" + encoded
    return "/desk/messages?group=" + encoded


def _message_notification_logs(user, read=0):
    logs = {}
    i = 0
    while i < len(MESSAGE_NOTIFICATION_LINK_PREFIXES):
        rows = frappe.get_all(
            "Notification Log",
            filters={
                "for_user": user,
                "type": MESSAGE_NOTIFICATION_TYPE,
                "link": ["like", MESSAGE_NOTIFICATION_LINK_PREFIXES[i]],
                "read": read,
            },
            fields=["name", "description", "link"],
            order_by="creation desc",
            limit_page_length=200,
        )
        j = 0
        while j < len(rows):
            logs[rows[j].name] = rows[j]
            j = j + 1
        i = i + 1
    return logs


def _unread_message_notification(member, group):
    logs = _message_notification_logs(member, read=0)
    names = list(logs.keys())
    i = 0
    while i < len(names):
        log = logs[names[i]]
        if message_group_from_notification(log.description, log.link) == str(group):
            return names[i]
        i = i + 1
    return None


def _upsert_message_notification(member, sender, sender_name, group, preview):
    group_name = _group_label(group)
    subject = "New message in " + str(group_name) + " from " + str(sender_name)
    description = _plain_preview(sender_name) + ": " + preview
    link = _notification_link_for(member, group)
    existing = _unread_message_notification(member, group)
    if existing:
        frappe.db.set_value("Notification Log", existing, {
            "title": subject,
            "subject": subject,
            "description": description,
            "email_content": description,
            "link": link,
            "app": MESSAGE_NOTIFICATION_APP,
            "document_type": None,
            "document_name": None,
            "from_user": sender,
        }, update_modified=False)
        return
    frappe.get_doc({
        "doctype": "Notification Log",
        "type": MESSAGE_NOTIFICATION_TYPE,
        "title": subject,
        "subject": subject,
        "description": description,
        "email_content": description,
        "link": link,
        "app": MESSAGE_NOTIFICATION_APP,
        "for_user": member,
        "from_user": sender,
        "read": 0,
    }).insert(ignore_permissions=True)


def _notify_group_members(sender, sender_name, group, message):
    preview = _message_preview(message)
    group_name = _group_label(group)
    members = _get_group_member_users(group)
    i = 0
    while i < len(members):
        member = members[i]
        if member == sender or not _user_enabled(member):
            i = i + 1
            continue
        _upsert_message_notification(member, sender, sender_name, group, preview)
        frappe.publish_realtime(
            event="messages_new_message_toast",
            message={
                "group": group,
                "group_name": group_name,
                "sender": sender,
                "sender_name": sender_name,
                "preview": preview,
                "link": _notification_link_for(member, group),
            },
            user=member,
            after_commit=True,
        )
        i = i + 1


def _mark_message_notifications_read(user, group):
    logs = _message_notification_logs(user, read=0)
    names = list(logs.keys())
    i = 0
    while i < len(names):
        log = logs[names[i]]
        if message_group_from_notification(log.description, log.link) == str(group):
            frappe.db.set_value("Notification Log", names[i], "read", 1, update_modified=False)
        i = i + 1


def _notification_unread_count(user):
    return frappe.db.count("Notification Log", {"for_user": user, "read": 0})


@frappe.whitelist()
def send_group_message(group, text=None, message_type="text", contact_user=None, sticker=None):
    user = _validate_group_for_send(group, "send messages")
    msg_type = str(message_type or "text")
    if msg_type not in ("text", "contact", "sticker"):
        frappe.throw("Unsupported message type.")
    caption = _clean_caption(text)
    contact = ""
    sticker_value = ""
    if msg_type == "contact":
        contact = str(contact_user or "").strip()
        if not contact:
            frappe.throw("Please choose a contact.")
        if not is_group_member(contact, group):
            frappe.throw("That contact is not a member of this group.", frappe.PermissionError)
    if msg_type == "sticker":
        sticker_value = str(sticker or "").strip()
        if sticker_value not in ALLOWED_STICKERS:
            frappe.throw("Sticker is not supported.")
        caption = sticker_value
    if msg_type == "text" and not caption:
        frappe.throw("Message cannot be empty.")
    if len(caption) > 2000:
        frappe.throw("Message is too long (max 2000 characters).")
    doc = frappe.get_doc({
        "doctype": "Group Message",
        "group": group,
        "sender": user,
        "message_text": caption,
        "message_type": msg_type,
        "contact_user": contact or None,
        "poll_question": None,
        "is_voice_note": 0,
        "sent_at": now_datetime()
    })
    doc.insert(ignore_permissions=True)
    _mark_sender_message_read(user, group, doc.creation)
    frappe.db.commit()
    payload = _serialize_message(doc, user)
    _publish_new_message(payload, group, user)
    return payload


@frappe.whitelist()
def upload_group_attachment(group=None, caption=None, voice_note=None):
    group = group or frappe.form_dict.get("group")
    caption = caption if caption is not None else frappe.form_dict.get("caption")
    voice_note = voice_note if voice_note is not None else frappe.form_dict.get("voice_note")
    user = _validate_group_for_send(group, "send files")
    files = getattr(frappe.local.request, "files", None)
    uploaded = files.get("file") if files else None
    if not uploaded:
        frappe.throw("Please choose a file.")
    content = uploaded.read()
    original_name = getattr(uploaded, "filename", "") or getattr(uploaded, "name", "file")
    declared_mime = getattr(uploaded, "content_type", "") or ""
    category, safe_name, mime_type = _validate_attachment(content, original_name, declared_mime)
    msg = _clean_caption(caption)
    if len(msg) > 2000:
        frappe.throw("Message is too long (max 2000 characters).")
    voice_value = str(voice_note or "").lower()
    is_voice = 1 if category == "audio" and voice_value in ("1", "true", "yes") else 0
    doc = frappe.get_doc({
        "doctype": "Group Message",
        "group": group,
        "sender": user,
        "message_text": msg,
        "message_type": "file" if category == "document" else category,
        "file_name": safe_name,
        "file_size": len(content),
        "mime_type": mime_type,
        "is_voice_note": is_voice,
        "sent_at": now_datetime()
    })
    doc.insert(ignore_permissions=True)
    stored_name = _safe_filename(doc.name + "-" + safe_name)
    file_data = save_file_on_filesystem(stored_name, content, content_type=mime_type, is_private=1)
    file_doc = frappe.get_doc({
        "doctype": "File",
        "file_name": stored_name,
        "file_url": file_data.get("file_url"),
        "file_size": len(content),
        "content_hash": get_content_hash(content),
        "mime_type": mime_type,
        "is_private": 1,
        "attached_to_doctype": "Group Message",
        "attached_to_name": doc.name,
        "attached_to_field": "attachment_file",
    }).insert(ignore_permissions=True)
    frappe.db.set_value("Group Message", doc.name, "attachment_file", file_doc.name, update_modified=False)
    doc = frappe.get_doc("Group Message", doc.name)
    _mark_sender_message_read(user, group, doc.creation)
    frappe.db.commit()
    payload = _serialize_message(doc, user)
    _publish_new_message(payload, group, user)
    return payload


@frappe.whitelist()
def download_group_file(message_name=None, inline=0):
    user = frappe.session.user
    if user == "Guest":
        frappe.throw("Please login.", frappe.PermissionError)
    message_name = message_name or frappe.form_dict.get("message_name")
    if not message_name or not frappe.db.exists("Group Message", message_name):
        frappe.throw("File not found.", frappe.DoesNotExistError)
    row = frappe.db.get_value(
        "Group Message",
        message_name,
        ["group", "sender", "attachment_file", "file_name", "mime_type", "message_type"],
        as_dict=True,
    )
    if not row or not row.attachment_file:
        frappe.throw("File not found.", frappe.DoesNotExistError)
    if not is_group_member(user, row.group):
        frappe.throw("You do not have permission to download this file.", frappe.PermissionError)
    file_doc = frappe.get_doc("File", row.attachment_file)
    if not file_doc.is_private:
        frappe.throw("File is not private.", frappe.PermissionError)
    inline_value = str(inline or frappe.form_dict.get("inline") or "0").lower()
    can_inline = inline_value in ("1", "true", "yes") and row.message_type in ("image", "video", "audio")
    frappe.local.response.filename = _safe_filename(row.file_name or file_doc.file_name or "file")
    frappe.local.response.filecontent = file_doc.get_content()
    frappe.local.response.content_type = row.mime_type or "application/octet-stream"
    frappe.local.response.display_content_as = "inline" if can_inline else "attachment"
    frappe.local.response.type = "download"
    if hasattr(frappe.local, "response_headers"):
        frappe.local.response_headers.set("Cache-Control", "private, no-store")
        frappe.local.response_headers.set("X-Content-Type-Options", "nosniff")
    return None


@frappe.whitelist()
def create_group_poll(group=None, question=None, options=None, caption=None):
    group = group or frappe.form_dict.get("group")
    question = question if question is not None else frappe.form_dict.get("question")
    options = options if options is not None else frappe.form_dict.get("options")
    caption = caption if caption is not None else frappe.form_dict.get("caption")
    user = _validate_group_for_send(group, "create polls")
    question = _clean_caption(question)
    if len(question) < 1 or len(question) > 200:
        frappe.throw("Poll question must be between 1 and 200 characters.")
    if isinstance(options, str):
        options = frappe.parse_json(options)
    if not isinstance(options, (list, tuple)):
        frappe.throw("Poll options must be a list.")
    if len(options) < 2 or len(options) > 6:
        frappe.throw("Poll must have between 2 and 6 options.")
    clean_options = []
    i = 0
    while i < len(options):
        text = _clean_caption(options[i])
        if not text or len(text) > 100:
            frappe.throw("Each poll option must be between 1 and 100 characters.")
        clean_options.append(text)
        i = i + 1
    msg = _clean_caption(caption)
    if len(msg) > 2000:
        frappe.throw("Message is too long (max 2000 characters).")
    poll_doc = frappe.get_doc({
        "doctype": "Group Poll",
        "group": group,
        "question": question,
        "created_by": user,
        "is_closed": 0,
        "options": [],
    })
    i = 0
    while i < len(clean_options):
        poll_doc.append("options", {
            "doctype": "Group Poll Option",
            "option_text": clean_options[i],
            "option_index": i + 1,
        })
        i = i + 1
    poll_doc.insert(ignore_permissions=True)
    message_doc = frappe.get_doc({
        "doctype": "Group Message",
        "group": group,
        "sender": user,
        "message_text": msg,
        "message_type": "poll",
        "poll": poll_doc.name,
        "poll_question": question,
        "sent_at": now_datetime(),
    })
    message_doc.insert(ignore_permissions=True)
    frappe.db.set_value("Group Poll", poll_doc.name, "message", message_doc.name, update_modified=False)
    _mark_sender_message_read(user, group, message_doc.creation)
    frappe.db.commit()
    payload = _serialize_message(message_doc, user)
    _publish_new_message(payload, group, user)
    return payload


@frappe.whitelist()
def vote_group_poll(poll=None, option_index=None):
    user = frappe.session.user
    if user == "Guest":
        frappe.throw("Please login.", frappe.PermissionError)
    poll = poll or frappe.form_dict.get("poll")
    option_index = option_index if option_index is not None else frappe.form_dict.get("option_index")
    if not poll or not frappe.db.exists("Group Poll", poll):
        frappe.throw("Poll not found.", frappe.DoesNotExistError)
    poll_doc = frappe.get_doc("Group Poll", poll)
    if not is_group_member(user, poll_doc.group):
        frappe.throw("You do not have permission to vote in this poll.", frappe.PermissionError)
    if poll_doc.is_closed:
        frappe.throw("This poll is closed.")
    remove_vote = False
    index = 0
    try:
        index = int(option_index)
    except Exception:
        value = str(option_index or "").strip().lower()
        if value in ("remove", "none", ""):
            remove_vote = True
        else:
            frappe.throw("Please choose a valid option.")
    if index == 0:
        remove_vote = True
    elif not remove_vote and (index < 1 or index > len(poll_doc.options or [])):
        frappe.throw("Please choose a valid option.")
    name = str(poll) + "-" + str(user)
    if remove_vote:
        if frappe.db.exists("Group Poll Vote", name):
            frappe.delete_doc("Group Poll Vote", name, force=True, ignore_permissions=True, delete_permanently=True)
    elif frappe.db.exists("Group Poll Vote", name):
        vote = frappe.get_doc("Group Poll Vote", name)
        vote.option_index = index
        vote.save(ignore_permissions=True)
    else:
        frappe.get_doc({
            "doctype": "Group Poll Vote",
            "name": name,
            "poll": poll,
            "user": user,
            "option_index": index,
        }).insert(ignore_permissions=True)
    frappe.db.commit()
    members = _get_group_member_users(poll_doc.group)
    i = 0
    while i < len(members):
        result = _serialize_poll(poll, members[i])
        frappe.publish_realtime(
            event="group_poll_updated",
            message={"poll": poll, "group": poll_doc.group, "result": result},
            user=members[i],
        )
        i = i + 1
    return _serialize_poll(poll, user)


@frappe.whitelist()
def close_group_poll(poll=None):
    user = frappe.session.user
    if user == "Guest":
        frappe.throw("Please login.", frappe.PermissionError)
    poll = poll or frappe.form_dict.get("poll")
    if not poll or not frappe.db.exists("Group Poll", poll):
        frappe.throw("Poll not found.", frappe.DoesNotExistError)
    poll_doc = frappe.get_doc("Group Poll", poll)
    if not is_group_member(user, poll_doc.group):
        frappe.throw("You do not have permission to close this poll.", frappe.PermissionError)
    if poll_doc.created_by != user:
        frappe.throw("Only the poll creator can close this poll.", frappe.PermissionError)
    poll_doc.is_closed = 1
    poll_doc.save(ignore_permissions=True)
    frappe.db.commit()
    members = _get_group_member_users(poll_doc.group)
    i = 0
    while i < len(members):
        result = _serialize_poll(poll, members[i])
        frappe.publish_realtime(
            event="group_poll_updated",
            message={"poll": poll, "group": poll_doc.group, "result": result},
            user=members[i],
        )
        i = i + 1
    return _serialize_poll(poll, user)


@frappe.whitelist()
def mark_group_read(group):
    user = frappe.session.user
    if user == "Guest":
        frappe.throw("Please login.", frappe.PermissionError)
    _ensure_class_groups_for_request()
    ensure_default_staff_group()
    ensure_all_members_group()
    if not group:
        frappe.throw("Group is required.")
    if not frappe.db.exists("Message Group", group):
        frappe.throw("Group not found.", frappe.DoesNotExistError)
    if not is_group_member(user, group):
        frappe.throw("You do not have permission.", frappe.PermissionError)
    existing = frappe.db.get_value("Group Message Read", {"user": user, "group": group}, "name")
    nowt = now_datetime()
    if existing:
        frappe.db.set_value("Group Message Read", existing, "last_read_on", nowt)
    else:
        frappe.get_doc({
            "doctype": "Group Message Read",
            "user": user,
            "group": group,
            "last_read_on": nowt
        }).insert(ignore_permissions=True)
    _mark_message_notifications_read(user, group)
    frappe.db.commit()
    return {"ok": True, "notification_unread_count": _notification_unread_count(user)}


@frappe.whitelist()
def set_group_archived(group, archived=1):
    user = frappe.session.user
    if user == "Guest":
        frappe.throw("Please login.", frappe.PermissionError)
    _ensure_class_groups_for_request()
    ensure_default_staff_group()
    ensure_all_members_group()
    if not group:
        frappe.throw("Group is required.")
    if not frappe.db.exists("Message Group", group):
        frappe.throw("Group not found.", frappe.DoesNotExistError)
    if not is_group_member(user, group):
        frappe.throw("You do not have permission.", frappe.PermissionError)
    value = 1 if cint(archived) else 0
    existing = frappe.db.get_value("Group Message Read", {"user": user, "group": group}, "name")
    if existing:
        frappe.db.set_value("Group Message Read", existing, "archived", value)
    else:
        frappe.get_doc({
            "doctype": "Group Message Read",
            "user": user,
            "group": group,
            "archived": value,
        }).insert(ignore_permissions=True)
    frappe.db.commit()
    return {"ok": True, "archived": bool(value), "group": group}


@frappe.whitelist()
def get_group_shared_items(group, limit=5, offset=0, kind="all"):
    user = frappe.session.user
    if user == "Guest":
        frappe.throw("Please login.", frappe.PermissionError)
    _ensure_class_groups_for_request()
    ensure_default_staff_group()
    ensure_all_members_group()
    if not group:
        frappe.throw("Group is required.")
    if not frappe.db.exists("Message Group", group):
        frappe.throw("Group not found.", frappe.DoesNotExistError)
    if not is_group_member(user, group):
        frappe.throw("You do not have permission to view this group.", frappe.PermissionError)
    lim = cint(limit)
    if lim < 1:
        lim = 5
    if lim > 100:
        lim = 100
    off = cint(offset)
    if off < 0:
        off = 0
    section = str(kind or "all").strip().lower()
    if section not in ("all", "file", "link"):
        section = "all"
    fields = [
        "name",
        "sender",
        "message_text",
        "message_type",
        "attachment_file",
        "file_name",
        "file_size",
        "mime_type",
        "is_voice_note",
        "sent_at",
        "creation",
    ]
    file_items = []
    link_items = []
    if section in ("all", "file"):
        rows = frappe.get_all(
            "Group Message",
            filters=[["group", "=", group], ["attachment_file", "is", "set"]],
            fields=fields,
            order_by="creation desc",
            limit_page_length=1000,
        )
        i = 0
        while i < len(rows):
            row = rows[i]
            encoded = quote(str(row.name or ""), safe="")
            file_items.append({
                "kind": "file",
                "message_name": row.name or "",
                "file_name": row.file_name or "Attachment",
                "file_size": int(row.file_size or 0),
                "mime_type": row.mime_type or "application/octet-stream",
                "message_type": row.message_type or "file",
                "is_voice_note": bool(row.is_voice_note),
                "sent_at": str(row.sent_at or row.creation or ""),
                "download_url": "/api/method/backend_custom_app.api.messages.download_group_file?message_name=" + encoded,
                "_sort_at": str(row.creation or row.sent_at or ""),
            })
            i = i + 1
    if section in ("all", "link"):
        rows = frappe.get_all(
            "Group Message",
            filters=[["group", "=", group], ["message_text", "like", "%http%"]],
            fields=fields,
            order_by="creation desc",
            limit_page_length=1000,
        )
        i = 0
        while i < len(rows):
            row = rows[i]
            urls = _shared_links_from_text(row.message_text)
            j = 0
            while j < len(urls):
                url = urls[j]
                link_items.append({
                    "kind": "link",
                    "message_name": row.name or "",
                    "url": url,
                    "host": urlparse(url).netloc or url,
                    "sent_at": str(row.sent_at or row.creation or ""),
                    "_sort_at": str(row.creation or row.sent_at or ""),
                })
                j = j + 1
            i = i + 1
    file_count = len(file_items)
    link_count = len(link_items)
    if section == "file":
        items = file_items
    elif section == "link":
        items = link_items
    else:
        items = file_items + link_items
        items.sort(key=lambda item: (item["_sort_at"], item["message_name"]), reverse=True)
    count = len(items)
    page = items[off:off + lim]
    i = 0
    while i < len(page):
        page[i].pop("_sort_at", None)
        i = i + 1
    return {
        "items": page,
        "count": count,
        "file_count": file_count,
        "link_count": link_count,
        "has_more": count > off + lim,
        "limit": lim,
        "offset": off,
        "kind": section,
    }


@frappe.whitelist()
def delete_group_message(message_name):
    user = frappe.session.user
    if user == "Guest":
        frappe.throw("Please login.", frappe.PermissionError)
    _ensure_class_groups_for_request()
    ensure_default_staff_group()
    ensure_all_members_group()
    if not message_name:
        frappe.throw("Message is required.")
    if not frappe.db.exists("Group Message", message_name):
        frappe.throw("Message not found.", frappe.DoesNotExistError)
    msg = frappe.db.get_value("Group Message", message_name, ["sender", "group"], as_dict=True)
    if not msg:
        frappe.throw("Message not found.", frappe.DoesNotExistError)
    if msg.sender != user:
        frappe.throw("You can only delete your own messages.", frappe.PermissionError)
    if not is_group_member(user, msg.group):
        frappe.throw("You do not have permission.", frappe.PermissionError)
    doc = frappe.get_doc("Group Message", message_name)
    _delete_message_related(doc)
    frappe.delete_doc("Group Message", message_name, ignore_permissions=True)
    frappe.db.commit()
    return {"ok": True}


@frappe.whitelist()
def get_message_socket_config():
    if frappe.session.user == "Guest":
        frappe.throw("Please login.", frappe.PermissionError)
    return {
        "sitename": frappe.local.site,
        "socketio_port": frappe.conf.socketio_port or 9000,
        "developer_mode": bool(frappe.conf.developer_mode),
    }


def _is_class_group(group):
    return frappe.db.get_value("Message Group", group, "group_type") == "Class"


def _require_group_admin(group):
    _ensure_class_groups_for_request()
    user = _require_logged_in_user()
    if not group:
        frappe.throw("Group is required.")
    group_doc = _group_doc(group, ["name", "group_type", "student_group", "owner"])
    if not group_doc:
        frappe.throw("Group not found.", frappe.DoesNotExistError)
    if group_doc.group_type == CUSTOM_GROUP_TYPE:
        if not _can_manage_custom_group(user, group_doc):
            frappe.throw("You do not have permission to manage group members.", frappe.PermissionError)
        return user
    if not _is_message_admin(user):
        frappe.throw("You do not have permission to manage group members.", frappe.PermissionError)
    if group_doc.group_type == "Class":
        return user
    if not is_group_member(user, group):
        frappe.throw("You do not have permission to manage group members.", frappe.PermissionError)
    return user


def _parse_member_list(members):
    if isinstance(members, str):
        try:
            members = frappe.parse_json(members)
        except Exception:
            frappe.throw("Members must be a valid JSON list.")
    if not isinstance(members, (list, tuple)):
        frappe.throw("Members must be a list.")
    result = []
    seen = {}
    i = 0
    while i < len(members):
        member = str(members[i] or "").strip()
        if member and member not in seen:
            seen[member] = 1
            result.append(member)
        i = i + 1
    return result


@frappe.whitelist()
def get_removed_group_members(group):
    _require_group_admin(group)
    rows = frappe.get_all(
        REMOVED_MEMBER_DOCTYPE,
        filters={"group": group},
        fields=["name", "group", "user", "removed_by", "removed_on", "reason"],
        order_by="removed_on desc",
    )
    result = []
    i = 0
    while i < len(rows):
        r = rows[i]
        result.append({
            "name": r.name,
            "group": r.group,
            "user": r.user,
            "display_name": _user_display_name(r.user),
            "removed_by": r.removed_by,
            "removed_by_name": _user_display_name(r.removed_by) if r.removed_by else "",
            "removed_on": str(r.removed_on) if r.removed_on else "",
            "reason": r.reason or "",
        })
        i = i + 1
    return result


@frappe.whitelist()
def remove_group_member(group, member):
    user = _require_group_admin(group)
    if not member:
        frappe.throw("Member is required.")
    if member == user:
        frappe.throw("You cannot remove yourself.", frappe.PermissionError)
    if member == "Administrator":
        frappe.throw("Administrator cannot be removed.", frappe.PermissionError)
    if _is_class_group(group) and member not in _get_group_member_users(group):
        frappe.throw("User is not a member of this class group.", frappe.PermissionError)
    group_doc = _group_doc(group)
    if group_doc and group_doc.group_type == CUSTOM_GROUP_TYPE and member not in _get_group_member_users(group):
        frappe.throw("User is not a member of this group.", frappe.PermissionError)
    existing = frappe.db.exists(
        REMOVED_MEMBER_DOCTYPE,
        {"group": group, "user": member},
    )
    if not existing:
        frappe.get_doc({
            "doctype": REMOVED_MEMBER_DOCTYPE,
            "group": group,
            "user": member,
            "removed_by": user,
            "removed_on": now_datetime(),
        }).insert(ignore_permissions=True)
    _clear_message_cache()
    frappe.publish_realtime(
        event="messages_group_access_changed",
        message={"group": group, "user": member, "removed": True},
        user=member,
        after_commit=True,
    )
    frappe.db.commit()
    return {"ok": True}


@frappe.whitelist()
def restore_group_member(group, member):
    user = _require_group_admin(group)
    if not member:
        frappe.throw("Member is required.")
    existing = frappe.db.exists(
        REMOVED_MEMBER_DOCTYPE,
        {"group": group, "user": member},
    )
    if existing:
        frappe.delete_doc(REMOVED_MEMBER_DOCTYPE, existing, ignore_permissions=True)
    _clear_message_cache()
    frappe.publish_realtime(
        event="messages_group_access_changed",
        message={"group": group, "user": member, "removed": False},
        user=member,
        after_commit=True,
    )
    frappe.db.commit()
    return {"ok": True}


@frappe.whitelist()
def bulk_remove_group_members(group, members):
    user = _require_group_admin(group)
    requested = _parse_member_list(members)
    removed = dict(_get_removed_users(group))
    class_group = _is_class_group(group)
    group_doc = _group_doc(group)
    custom_group = bool(group_doc and group_doc.group_type == CUSTOM_GROUP_TYPE)
    allowed_members = _get_group_member_users(group) if class_group or custom_group else []
    succeeded = []
    failed = []
    i = 0
    while i < len(requested):
        member = requested[i]
        reason = ""
        if member == user:
            reason = "You cannot remove yourself."
        elif member == "Administrator":
            reason = "Administrator cannot be removed."
        elif member in removed:
            reason = "Already removed from this group."
        elif class_group and member not in allowed_members:
            reason = "User is not a member of this class group."
        elif custom_group and member not in allowed_members:
            reason = "User is not a member of this group."
        elif not frappe.db.exists("User", {"name": member, "enabled": 1}):
            reason = "User not found or disabled."
        if reason:
            failed.append({"user": member, "skipped": reason})
        else:
            frappe.get_doc({
                "doctype": REMOVED_MEMBER_DOCTYPE,
                "group": group,
                "user": member,
                "removed_by": user,
                "removed_on": now_datetime(),
            }).insert(ignore_permissions=True)
            removed[member] = 1
            succeeded.append(member)
            frappe.publish_realtime(
                event="messages_group_access_changed",
                message={"group": group, "user": member, "removed": True},
                user=member,
                after_commit=True,
            )
        i = i + 1
    if succeeded:
        _clear_message_cache()
        frappe.db.commit()
    return {
        "requested": requested,
        "succeeded": succeeded,
        "failed": failed,
        "skipped": {item["user"]: item["skipped"] for item in failed},
    }


@frappe.whitelist()
def bulk_restore_group_members(group, members):
    _require_group_admin(group)
    requested = _parse_member_list(members)
    succeeded = []
    failed = []
    i = 0
    while i < len(requested):
        member = requested[i]
        existing = frappe.db.exists(
            REMOVED_MEMBER_DOCTYPE,
            {"group": group, "user": member},
        )
        if not existing:
            failed.append({"user": member, "skipped": "User is not in the removed list."})
        else:
            frappe.delete_doc(REMOVED_MEMBER_DOCTYPE, existing, ignore_permissions=True)
            succeeded.append(member)
            frappe.publish_realtime(
                event="messages_group_access_changed",
                message={"group": group, "user": member, "removed": False},
                user=member,
                after_commit=True,
            )
        i = i + 1
    if succeeded:
        _clear_message_cache()
        frappe.db.commit()
    return {
        "requested": requested,
        "succeeded": succeeded,
        "failed": failed,
        "skipped": {item["user"]: item["skipped"] for item in failed},
    }


def _delete_custom_group_related(group):
    messages = frappe.get_all("Group Message", filters={"group": group}, pluck="name")
    i = 0
    while i < len(messages):
        doc = frappe.get_doc("Group Message", messages[i])
        _delete_message_related(doc)
        frappe.delete_doc("Group Message", messages[i], force=True, ignore_permissions=True, delete_permanently=True)
        i = i + 1
    polls = frappe.get_all("Group Poll", filters={"group": group}, pluck="name")
    i = 0
    while i < len(polls):
        votes = frappe.get_all("Group Poll Vote", filters={"poll": polls[i]}, pluck="name")
        j = 0
        while j < len(votes):
            if frappe.db.exists("Group Poll Vote", votes[j]):
                frappe.delete_doc("Group Poll Vote", votes[j], force=True, ignore_permissions=True, delete_permanently=True)
            j = j + 1
        if frappe.db.exists("Group Poll", polls[i]):
            frappe.delete_doc("Group Poll", polls[i], force=True, ignore_permissions=True, delete_permanently=True)
        i = i + 1
    frappe.db.delete("Group Message Read", {"group": group})
    frappe.db.delete(REMOVED_MEMBER_DOCTYPE, {"group": group})
    frappe.db.delete(
        MESSAGE_GROUP_MEMBER_DOCTYPE,
        {"parent": group, "parenttype": "Message Group", "parentfield": "members"},
    )
    frappe.delete_doc("Message Group", group, force=True, ignore_permissions=True, delete_permanently=True)


@frappe.whitelist()
def create_message_group(group_name, description=None, members=None):
    user = _require_logged_in_user()
    if not _can_create_message_group(user):
        frappe.throw("You do not have permission to create message groups.", frappe.PermissionError)
    name = _clean_caption(group_name)
    if len(name) < 2 or len(name) > 60:
        frappe.throw("Group name must be between 2 and 60 characters.")
    requested = _parse_member_list(members or [])
    member_users = _filter_enabled_users(requested)
    doc = frappe.get_doc({
        "doctype": "Message Group",
        "group_name": name,
        "group_type": CUSTOM_GROUP_TYPE,
        "is_default": 0,
        "description": _clean_caption(description),
        "owner": user,
    })
    _append_custom_group_member(doc, user, user, is_admin=1)
    i = 0
    while i < len(member_users):
        if member_users[i] != user:
            _append_custom_group_member(doc, member_users[i], user)
        i = i + 1
    doc.insert(ignore_permissions=True)
    _clear_message_cache()
    _publish_group_list_changed(doc.name, _get_group_member_users(doc.name))
    return {
        "name": doc.name,
        "group": doc.name,
        "group_name": name,
        "group_type": CUSTOM_GROUP_TYPE,
    }


@frappe.whitelist()
def add_message_group_members(group, members):
    user, group_doc = _require_custom_group(group)
    if not _can_manage_custom_group(user, group_doc):
        frappe.throw("You do not have permission to manage group members.", frappe.PermissionError)
    requested = _parse_member_list(members)
    member_users = _filter_enabled_users(requested)
    doc = frappe.get_doc("Message Group", group_doc.name)
    existing_members = _get_group_member_users(group_doc.name)
    added = []
    restored = []
    i = 0
    while i < len(member_users):
        member = member_users[i]
        if member not in existing_members:
            _append_custom_group_member(doc, member, user)
            added.append(member)
            if frappe.db.exists(REMOVED_MEMBER_DOCTYPE, {"group": group_doc.name, "user": member}):
                _delete_removed_member(group_doc.name, member)
                restored.append(member)
        i = i + 1
    if added:
        doc.save(ignore_permissions=True)
        _clear_message_cache()
        _publish_group_list_changed(group_doc.name, added)
        i = 0
        while i < len(added):
            _publish_group_access_changed(group_doc.name, added[i], False)
            i = i + 1
    return {
        "ok": True,
        "group": group_doc.name,
        "added": added,
        "restored": restored,
    }


@frappe.whitelist()
def get_message_group_candidates(group=None, search=None, limit=50):
    user = _require_logged_in_user()
    if not _can_manage_message_candidates(user):
        frappe.throw("You do not have permission to view group candidates.", frappe.PermissionError)
    group_doc = None
    already = {}
    if group:
        group_doc = _group_doc(group)
        if not group_doc:
            frappe.throw("Group not found.", frappe.DoesNotExistError)
        if group_doc.group_type != CUSTOM_GROUP_TYPE:
            frappe.throw("Only custom groups support this action.", frappe.PermissionError)
        if not _can_manage_custom_group(user, group_doc):
            frappe.throw("You do not have permission to manage group members.", frappe.PermissionError)
        members = _get_group_member_users(group_doc.name)
        i = 0
        while i < len(members):
            already[members[i]] = 1
            i = i + 1
    term = str(search or "").strip().lower()
    lim = cint(limit)
    if lim < 1:
        lim = 50
    if lim > 200:
        lim = 200
    candidates = []
    seen = {}
    groups = [_get_admin_users(), _get_instructor_users(), _get_student_users()]
    g = 0
    while g < len(groups):
        i = 0
        while i < len(groups[g]):
            uname = groups[g][i]
            if uname and uname not in seen:
                full_name = _user_display_name(uname)
                email = frappe.db.get_value("User", uname, "email") or uname
                matches = (
                    not term
                    or term in str(uname).lower()
                    or term in full_name.lower()
                    or term in str(email).lower()
                )
                if matches:
                    seen[uname] = 1
                    candidates.append({
                        "user": uname,
                        "full_name": full_name,
                        "email": email,
                        "role_label": _get_role_label(uname),
                        "already_member": bool(already.get(uname)),
                    })
            i = i + 1
        g = g + 1
    return candidates[:lim]


@frappe.whitelist()
def clear_group_chat(group):
    user, group_doc = _require_group_membership(group, "clear chat")
    cleared_at = now_datetime()
    _upsert_group_message_read(user, group_doc.name, "cleared_at", cleared_at)
    frappe.publish_realtime(
        event="messages_group_cleared",
        message={"group": group_doc.name, "cleared_at": str(cleared_at)},
        user=user,
        after_commit=True,
    )
    frappe.db.commit()
    return {"ok": True, "group": group_doc.name, "cleared_at": str(cleared_at)}


@frappe.whitelist()
def exit_message_group(group):
    user, group_doc = _require_group_membership(group, "exit")
    owner = group_doc.owner or ""
    new_owner = ""
    if group_doc.group_type == CUSTOM_GROUP_TYPE:
        _remove_custom_group_member(group_doc.name, user)
        _clear_message_cache()
        if group_doc.owner == user:
            remaining = _get_custom_group_members(group_doc.name)
            i = 0
            while i < len(remaining) and not new_owner:
                row = remaining[i]
                if row.get("user") != user and row.get("is_admin"):
                    new_owner = row.get("user")
                i = i + 1
            i = 0
            while i < len(remaining) and not new_owner:
                member = remaining[i].get("user")
                if member and member != user and (_is_message_admin(member) or is_instructor_message_role(member)):
                    new_owner = member
                i = i + 1
            if new_owner:
                frappe.db.set_value("Message Group", group_doc.name, "owner", new_owner, update_modified=False)
                owner = new_owner
    else:
        _upsert_removed_member(group_doc.name, user, user, reason="Left")
    _clear_message_cache()
    _publish_group_access_changed(group_doc.name, user, True)
    _publish_group_list_changed(group_doc.name, [user])
    if new_owner:
        _publish_group_list_changed(group_doc.name, [new_owner])
    frappe.db.commit()
    return {"ok": True, "group": group_doc.name, "owner": owner}


@frappe.whitelist()
def delete_message_group(group):
    user, group_doc = _require_custom_group(group)
    if not _can_delete_custom_group(user, group_doc):
        frappe.throw("Only the group owner or an administrator can delete this group.", frappe.PermissionError)
    members = _get_group_member_users(group_doc.name)
    _publish_group_deleted(group_doc.name, group_doc.group_name, members)
    frappe.db.savepoint("delete_message_group")
    try:
        _delete_custom_group_related(group_doc.name)
    except Exception:
        frappe.db.rollback(save_point="delete_message_group")
        raise
    _clear_message_cache()
    frappe.db.commit()
    return {"ok": True, "group": group_doc.name}
