
frappe.pages['education-dashboard'].on_page_load = function (wrapper) {
    const page = frappe.ui.make_app_page({
        parent: wrapper,
        title: 'Education Dashboard',
        single_column: true
    });

    page.main.css({
        padding: '0',
        margin: '0',
        overflow: 'hidden'
    });

    const frame = document.createElement('iframe');

    frame.src = 'http://erpnext.localhost:8080/frontend/#/';
    frame.title = 'Education Dashboard';
    frame.style.width = '100%';
    frame.style.height = 'calc(100vh - 170px)';
    frame.style.minHeight = '0';
    frame.style.border = '0';
    frame.style.display = 'block';

    page.main[0].appendChild(frame);
};
