frappe.pages['instructor-dashboard'].on_page_load = function(wrapper) {
	var page = frappe.ui.make_app_page({
		parent: wrapper,
		title: 'Instructor Dashboard',
		single_column: true
	});
}