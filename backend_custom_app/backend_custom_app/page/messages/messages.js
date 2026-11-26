frappe.pages['messages'].on_page_load = function (wrapper) {
    var page = frappe.ui.make_app_page({
        parent: wrapper,
        title: 'Messages',
        single_column: true
    });

    page.page_head.hide();
    page.main.css({
        padding: '0',
        margin: '0',
        overflow: 'hidden'
    });
    page.container.css({
        padding: '0',
        margin: '0',
        height: '100vh',
        overflow: 'hidden'
    });

    var frame = document.createElement('iframe');
    frame.title = 'Messages';
    frame.setAttribute('allow', 'camera; microphone');
    frame.style.width = '100%';
    frame.style.height = '100vh';
    frame.style.border = '0';
    frame.style.display = 'block';
    page.main[0].appendChild(frame);

    var frameVersion = Date.now();
    var lastFrameSrc = '';
    function frameSrc() {
        var query = new URLSearchParams(window.location.search);
        var group = query.get('group');
        if (!group && frappe.route_options && frappe.route_options.group) {
            group = frappe.route_options.group;
            delete frappe.route_options.group;
        }
        var hashQuery = 'embed=1';
        if (group) {
            hashQuery = hashQuery + '&group=' + encodeURIComponent(group);
        }
        return '/assets/frontend_custom_app/frontend/index.html?_=' + frameVersion + '#/messages?' + hashQuery;
    }
    function syncFrame() {
        var nextSrc = frameSrc();
        if (nextSrc === lastFrameSrc) {
            return;
        }
        lastFrameSrc = nextSrc;
        frame.src = nextSrc;
    }
    syncFrame();
    if (!window.messages_frame_sync_bound) {
        window.messages_frame_sync_bound = true;
        frappe.router.on('change', function(router) {
            if (router.current_route && router.current_route[0] === 'messages') {
                syncFrame();
            }
        });
    }

    function fitFrame() {
        var rect = frame.getBoundingClientRect();
        if (!rect.width && !rect.height) {
            return;
        }
        var available = Math.floor(window.innerHeight - rect.top);
        frame.style.height = Math.max(available, 240) + 'px';
    }

    fitFrame();
    $(window).off('resize.messagesFrame').on('resize.messagesFrame', fitFrame);
};