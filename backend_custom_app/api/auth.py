import frappe

from backend_custom_app.api.permissions import EDUCATION_USER_ROLE


@frappe.whitelist(allow_guest=True)
def get_csrf_token():
	"""Return the CSRF token of the current session.

	The portal is a static SPA asset, so ``window.csrf_token`` is never
	rendered server side. This endpoint plays the role of the Desk page:
	it generates the token (if the session does not have one yet) and
	persists it, so the frontend can echo it back as ``X-Frappe-CSRF-Token``
	on every unsafe request.
	"""
	return frappe.sessions.get_csrf_token()


@frappe.whitelist(allow_guest=True)
def create_user(full_name=None, email=None, password=None):
    """Create a new Frappe User from the frontend sign-up form."""
    if not full_name or not email or not password:
        frappe.throw("Full name, email, and password are required.")

    full_name = str(full_name).strip()
    email = str(email).strip()
    password = str(password)

    if not full_name:
        frappe.throw("Full name is required.")

    if not email:
        frappe.throw("Email is required.")

    if len(password) < 8:
        frappe.throw("Password must be at least 8 characters long.")

    if "@" not in email or "." not in email.split("@")[-1]:
        frappe.throw("Please enter a valid email address.")

    if frappe.db.exists("User", {"email": email}):
        frappe.throw("An account with this email already exists.")

    try:
        user = frappe.new_doc("User")
        user.email = email
        user.first_name = full_name
        user.enabled = 1
        user.send_welcome_email = 0

        # Least-privilege access only: no Desk User, System Manager,
        # Administrator or any other broad/default role. The user type is
        # derived by Frappe from the roles that are assigned here.
        user.append_roles(EDUCATION_USER_ROLE)
        user.new_password = password
        user.insert(ignore_permissions=True)
    except frappe.ValidationError:
        frappe.throw("Please choose a stronger, unique password. Common or weak passwords are not allowed.")

    return {
        "success": True,
        "message": "Account created successfully. Please sign in.",
    }
