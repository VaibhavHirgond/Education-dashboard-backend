"""Post-login redirect rules for the education portal.

``LoginManager.post_login`` runs the ``on_login`` trigger before
``set_user_info()`` resolves the redirect target, and
``frappe.website.utils.get_home_page`` honours
``frappe.local.flags.home_page`` ahead of Role / Portal Settings lookups.
Setting the flag here therefore steers the ``home_page`` value that the
standard login page (``templates/includes/login/login.js``) redirects to,
without touching any Frappe core file.

That flag only covers the ``get_home_page()`` branch though: for Website
Users ``set_user_info()`` evaluates ``get_default_path()`` *first*, which
resolves to ``/app/pwa-frappe`` (the sole ``add_to_apps_screen`` app) and is
301-redirected by ``website_redirects`` to ``/desk/pwa-frappe`` ("Not
Permitted").  :func:`rewrite_student_login_response` fixes that second
branch by rewriting the built login JSON from an ``after_request`` hook.

Rules:
  * A non-staff user whose login carries the Student role or is linked to
    an active Student record (``Student.user`` match) is sent to the
    Student Portal at ``origin + /student-portal#/``.
  * Administrators, instructors and other staff keep their existing
    redirect (Role home_page / Portal Settings / ``/desk``).
  * Saved or requested redirects (``redirect_to``, ``?redirect-to=``,
    ``localStorage.last_visited``) are never evaluated for students, so a
    poisoned value cannot bounce them to ``/desk`` / ``/app`` / ``/login``
    and an undefined ``redirect_to`` never reaches
    ``frappe.utils.sanitise_redirect`` (whose ``strip_url`` helper calls
    ``url.replace`` and throws on undefined values).

The existing ``get_website_user_home_page`` hook in
``frontend_custom_app.redirects`` stays as the fallback for flows that
never run this trigger (e.g. resumed sessions). There is no redirect
loop: the flag is never set for users without a student scope, and the
portal itself does not bounce authenticated users back to /login.
"""

import json
from urllib.parse import urlsplit

import frappe

from backend_custom_app.api.permissions import (
	get_instructor_record,
	get_student_record,
	is_staff,
)

STUDENT_PORTAL_PATH = "/student-portal"
# Exact post-login destination used by the custom login page wrapper:
# window.location.origin + STUDENT_PORTAL_HOME.
STUDENT_PORTAL_HOME = STUDENT_PORTAL_PATH + "#/"

# Destinations a student login must never be bounced back to: the PWA /
# desk redirect chain, and the login page itself.
BLOCKED_STUDENT_PATHS = ("/desk", "/app", "/login")


def redirect_student_after_login(login_manager=None):
	"""Set the post-login home page for student-portal accounts."""

	user = (
		getattr(login_manager, "user", None)
		or frappe.session.user
	)

	if not user or user == "Guest":
		return

	# Staff (Administrator / System Manager / Education Manager /
	# Academics User) always keep their desk redirect.
	if is_staff(user):
		return

	# A valid linked Student record or the bare Student role is enough;
	# instructors and other staff never take this branch.
	if "Student" in set(frappe.get_roles(user)) or get_student_record(user):
		frappe.local.flags.home_page = STUDENT_PORTAL_HOME
		return

	# Instructors keep their existing desk behaviour - never send them
	# to the student portal.
	if get_instructor_record(user):
		return


def _student_safe_redirect(value):
	"""True when ``value`` is a same-origin URL outside blocked paths.

	Protocol-relative URLs (``//evil.example``) and other origins are
	rejected; relative paths must not start with ``/desk``, ``/app`` or
	``/login`` (word-boundary check, so ``/apply-here`` stays valid).
	"""

	if not value or not isinstance(value, str):
		return False

	if value.startswith("//"):
		return False

	path = value

	if not value.startswith("/"):
		parsed = urlsplit(value)
		site = urlsplit(frappe.utils.get_url())
		if parsed.scheme not in ("http", "https") or parsed.netloc != site.netloc:
			return False
		path = parsed.path
		if parsed.query:
			path += "?" + parsed.query
		if parsed.fragment:
			path += "#" + parsed.fragment

	return not any(
		path == blocked
		or path.startswith(blocked + "/")
		or path.startswith(blocked + "?")
		or path.startswith(blocked + "#")
		for blocked in BLOCKED_STUDENT_PATHS
	)


def rewrite_student_login_response(response=None, request=None):
	"""``after_request`` hook: force student logins onto the portal.

	``set_user_info`` picks ``home_page`` with ``get_default_path()`` first
	for Website Users, which resolves to ``/app/pwa-frappe`` and redirects
	through ``website_redirects`` to ``/desk/pwa-frappe`` - the branch the
	``on_login`` flag above cannot reach.  This rewrites the already-built
	login JSON (the same condition ``LoginManager`` uses to detect a login
	POST) after the endpoint has run and before the response is sent, so
	both ``/api/method/login`` and the legacy ``cmd=login`` POST are
	covered.  The ``student_portal`` marker tells the custom login page to
	navigate to ``origin + /student-portal#/`` itself, before any
	saved/requested redirect is looked at; staff and non-student accounts
	are never touched.
	"""

	if response is None or getattr(response, "status_code", None) != 200:
		return

	path = getattr(request, "path", "") if request is not None else ""
	form_dict = getattr(frappe.local, "form_dict", None) or {}
	if path != "/api/method/login" and form_dict.get("cmd") != "login":
		return

	if not getattr(response, "is_json", False):
		return

	try:
		payload = json.loads(response.get_data(as_text=True))
	except (TypeError, ValueError):
		return

	# "Logged In" / "No App" are only ever set by set_user_info during a
	# real login (not resumes, signups, password resets or 2FA prompts).
	if not isinstance(payload, dict) or payload.get("message") not in (
		"Logged In",
		"No App",
	):
		return

	user = frappe.session.user
	if not user or user == "Guest" or is_staff(user):
		return

	if "Student" not in set(frappe.get_roles(user)) and not get_student_record(user):
		return

	payload["home_page"] = STUDENT_PORTAL_HOME
	payload["student_portal"] = True

	# Drop a cached redirect that would take the student back to desk/PWA.
	if payload.get("redirect_to") and not _student_safe_redirect(
		payload["redirect_to"]
	):
		payload.pop("redirect_to")

	response.set_data(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
