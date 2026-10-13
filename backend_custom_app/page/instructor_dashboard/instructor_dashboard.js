
frappe.pages['instructor-dashboard'].on_page_load = function (wrapper) {
    const page = frappe.ui.make_app_page({
        parent: wrapper,
        title: 'Instructor Dashboard',
        single_column: true
    });

    page.main.css({
        padding: '20px',
        background: '#f8fafc'
    });

    const container = $(`
        <div class="instructor-dashboard">
            <div class="dashboard-heading">
                <h3>Instructor Overview</h3>
                <p>Attendance and student summary for your assigned groups.</p>
            </div>

            <div class="dashboard-cards">
                <div class="dashboard-card">
                    <div class="card-label">Total Students</div>
                    <div class="card-value" id="total-students">—</div>
                </div>
                <div class="dashboard-card">
                    <div class="card-label">Present Today</div>
                    <div class="card-value" id="present-today">—</div>
                </div>
                <div class="dashboard-card">
                    <div class="card-label">Absent Today</div>
                    <div class="card-value" id="absent-today">—</div>
                </div>
                <div class="dashboard-card">
                    <div class="card-label">Attendance Today</div>
                    <div class="card-value" id="attendance-percentage">—</div>
                </div>
            </div>

            <div class="dashboard-section">
                <h4>Attendance Trend</h4>
                <div id="attendance-trend">
                    Loading attendance trend…
                </div>
            </div>

            <div class="dashboard-section">
                <h4>Quick Access</h4>
                <p>
                    <a href="/app/student-attendance"
                       class="btn btn-default">Open Student Attendance</a>
                    <a href="/app/assessment"
                       class="btn btn-default">Open Assessment</a>
                </p>
            </div>
        </div>
    `);

    page.main.append(container);

    if (!$('#instructor-dashboard-styles').length) {
        $('<style id="instructor-dashboard-styles">').text(`
            .instructor-dashboard { color: #1f2937; }
            .dashboard-heading { margin-bottom: 24px; }
            .dashboard-heading h3 { margin-bottom: 6px; }
            .dashboard-heading p { color: #64748b; }
            .dashboard-cards {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
                gap: 16px;
                margin-bottom: 24px;
            }
            .dashboard-card, .dashboard-section {
                background: white;
                border: 1px solid #e2e8f0;
                border-radius: 10px;
                padding: 20px;
            }
            .card-label { color: #64748b; font-size: 13px; }
            .card-value {
                font-size: 28px;
                font-weight: 600;
                margin-top: 8px;
            }
            .dashboard-section { margin-bottom: 20px; }
            .dashboard-section h4 { margin-top: 0; margin-bottom: 16px; }
        `).appendTo('head');
    }

    load_instructor_dashboard(container);
};

function load_instructor_dashboard(container) {
    frappe.call({
        method: 'backend_custom_app.api.education.get_dashboard',
        callback: function (response) {
            if (response.exc || !response.message) {
                container.find('#attendance-trend').text(
                    'Unable to load dashboard data. Check your Instructor permissions and server logs.'
                );
                return;
            }

            const data = response.message;

            if (data.mode !== 'instructor') {
                frappe.msgprint(
                    'This page is intended for users with the Instructor role.'
                );
                return;
            }

            container.find('#total-students').text(
                data.students_count ?? 0
            );

            // These values will be filled by the instructor-specific
            // attendance API enhancement in the next step.
            const attendance = data.attendance || {};

            container.find('#present-today').text(
                attendance.today_present ?? '—'
            );

            container.find('#absent-today').text(
                attendance.today_absent ?? '—'
            );

            container.find('#attendance-percentage').text(
                attendance.today_percentage != null
                    ? `${attendance.today_percentage}%`
                    : '—'
            );

            const trend = attendance.trend || [];

            if (!trend.length) {
                container.find('#attendance-trend').text(
                    'Daily attendance trend will appear after the backend provides the trend data.'
                );
                return;
            }

            const chart = $('<div>').css({
                display: 'flex',
                alignItems: 'flex-end',
                gap: '12px',
                height: '220px',
                padding: '12px 0'
            });

            trend.forEach(function (item) {
                const percentage = Math.max(
                    0,
                    Math.min(100, Number(item.percentage) || 0)
                );

                const column = $('<div>').css({
                    flex: '1',
                    minWidth: '0',
                    textAlign: 'center'
                });

                $('<div>').text(`${percentage}%`).css({
                    fontSize: '12px',
                    marginBottom: '6px'
                }).appendTo(column);

                $('<div>').css({
                    height: `${Math.max(4, percentage * 1.4)}px`,
                    background: '#4f46e5',
                    borderRadius: '5px 5px 0 0',
                    maxWidth: '56px',
                    margin: '0 auto'
                }).appendTo(column);

                $('<div>').text(item.date || '').css({
                    fontSize: '11px',
                    marginTop: '8px',
                    color: '#64748b'
                }).appendTo(column);

                chart.append(column);
            });

            container.find('#attendance-trend').empty().append(chart);
        }
    });
}
