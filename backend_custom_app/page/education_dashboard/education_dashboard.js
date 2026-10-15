
frappe.pages['education-dashboard'].on_page_load = function (wrapper) {
    const page = frappe.ui.make_app_page({
        parent: wrapper,
        title: 'Dashboard',
        single_column: true
    });

    page.main.css({
        padding: '0',
        margin: '0',
        overflow: 'hidden'
    });

    const frame = document.createElement('iframe');

    frame.title = 'Education Dashboard';
    frame.style.width = '100%';
    frame.style.height = '700px';
    frame.style.border = '0';
    frame.style.display = 'block';

    page.main[0].appendChild(frame);

    let dashboardHeight = null;

    function fitFrame() {
        const rect = frame.getBoundingClientRect();

        if (!rect.width && !rect.height) return;

        if (dashboardHeight !== null) {
            frame.style.height = dashboardHeight + 'px';
            return;
        }

        const available = Math.floor(window.innerHeight - rect.top - 8);
        frame.style.height = Math.max(available, 320) + 'px';
    }

    function setDashboardHeight(event) {
        if (event.origin !== window.location.origin || event.source !== frame.contentWindow) {
            return;
        }

        const message = event.data;
        const height = Number(message?.height);

        if (message?.type !== 'education-dashboard-height' || !Number.isFinite(height) || height <= 0) {
            return;
        }

        dashboardHeight = Math.ceil(height);
        frame.style.height = dashboardHeight + 'px';
    }

    async function loadDashboard() {
        try {
            const response = await fetch(
                '/api/method/backend_custom_app.api.context.get_context',
                { credentials: 'same-origin' }
            );

            if (!response.ok) {
                throw new Error('Failed to load user context');
            }

            const result = await response.json();
            const context = result.message;

            // Instructor takes priority over Education Manager.
            let route = '/';

            if (context?.is_instructor) {
                route = '/instructor';
            } else if (context?.is_student) {
                route = '/';
            } else if (context?.is_staff) {
                route = '/';
            }

            frame.src =
                '/assets/frontend_custom_app/frontend/index.html#' + route;
        } catch (error) {
            console.error('Unable to load the correct dashboard:', error);
            frame.src = '/assets/frontend_custom_app/frontend/index.html#/';
        }
    }

    window.addEventListener('message', setDashboardHeight);

    loadDashboard();
    fitFrame();

    window.addEventListener('resize', fitFrame);
};
