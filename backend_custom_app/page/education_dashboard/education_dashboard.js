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

    // Same-origin relative URL: works for every local and production
    // hostname without hardcoding a host, port or scheme.
    // Same origin also means the browser sends the active Frappe session
    // cookie with every request the embedded app makes.
    //
    // NOTE: Frappe loads the copy that lives inside the module folder
    // (backend_custom_app/backend_custom_app/page/education_dashboard).
    // Keep both copies identical.
    const frame = document.createElement('iframe');

    frame.src = '/assets/frontend_custom_app/frontend/index.html#/';
    frame.title = 'Education Dashboard';
    frame.style.width = '100%';
    frame.style.height = '700px';
    frame.style.border = '0';
    frame.style.display = 'block';

    page.main[0].appendChild(frame);

    // Give the dashboard exactly the space below the Desk page header.
    // The Vue app scrolls inside the frame when it is taller than the
    // window, so no postMessage based resizing is needed (and none can
    // ever fight with the layout).
    function fitFrame() {
        const rect = frame.getBoundingClientRect();

        // The Desk keeps page containers around while hidden; a hidden
        // frame reports an all-zero rect, so keep the last good size.
        if (!rect.width && !rect.height) {
            return;
        }

        const available = Math.floor(window.innerHeight - rect.top - 8);

        frame.style.height = Math.max(available, 320) + 'px';
    }

    fitFrame();
    window.addEventListener('resize', fitFrame);
};
