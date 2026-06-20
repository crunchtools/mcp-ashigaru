(function () {
    "use strict";

    var refreshTimer = null;

    function apiGet(path) {
        return new Promise(function (resolve, reject) {
            var http = new cockpit.http({ port: 8020, address: "localhost" });
            http.get(path)
                .then(function (data) { resolve(JSON.parse(data)); })
                .catch(function (err) { reject(err); });
        });
    }

    function apiGetText(path) {
        return new Promise(function (resolve, reject) {
            var http = new cockpit.http({ port: 8020, address: "localhost" });
            http.get(path)
                .then(function (data) { resolve(data); })
                .catch(function (err) { reject(err); });
        });
    }

    function apiDelete(path) {
        return new Promise(function (resolve, reject) {
            var http = new cockpit.http({ port: 8020, address: "localhost" });
            http.request({ method: "DELETE", path: path })
                .then(function (data) { resolve(data); })
                .catch(function (err) { reject(err); });
        });
    }

    function phaseBadge(phase) {
        var cls = "ashigaru-phase-" + (phase || "unknown").replace(/_/g, "-");
        return '<span class="pf-v6-c-label pf-m-compact ' + cls + '">' +
               '<span class="pf-v6-c-label__content">' + (phase || "unknown") + '</span></span>';
    }

    function sourceBadge(source) {
        return '<span class="ashigaru-source-badge">' + (source || "ashigaru") + '</span>';
    }

    function formatTime(ts) {
        if (!ts) return "";
        var d = new Date(ts);
        if (isNaN(d.getTime())) return ts;
        return d.toLocaleString();
    }

    function formatTimeShort(ts) {
        if (!ts) return "";
        var d = new Date(ts);
        if (isNaN(d.getTime())) return ts;
        return d.toLocaleTimeString();
    }

    function truncate(s, max) {
        max = max || 60;
        if (!s) return "";
        if (s.length <= max) return s;
        return s.substring(0, max - 3) + "...";
    }

    function escapeHtml(s) {
        if (!s) return "";
        return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    }

    // ---- Run List View -------------------------------------------------------

    function renderListPage() {
        var phases = [
            "queued", "cloning", "analyzing", "implementing", "validating",
            "building", "awaiting-review", "reviewing", "preview-live",
            "shipped", "failed", "escalated", "cancelled",
            "editing", "gating", "awaiting-approval", "on-dev"
        ];
        var phaseOptions = '<option value="">All phases</option>';
        for (var p = 0; p < phases.length; p++) {
            phaseOptions += '<option value="' + phases[p] + '">' + phases[p] + '</option>';
        }

        var html = '<div class="pf-v6-c-page">' +
            '<main class="pf-v6-c-page__main">' +
            '<section class="pf-v6-c-page__main-section">' +
            '<div class="pf-v6-c-content"><h1>Ashigaru Runners</h1></div>' +
            '</section>' +
            '<section class="pf-v6-c-page__main-section pf-m-light">' +
            '<div class="ashigaru-filter-bar">' +
            '<input type="text" id="filter-repo" placeholder="Filter by repo..." />' +
            '<select id="filter-phase">' + phaseOptions + '</select>' +
            '<button class="pf-v6-c-button pf-m-secondary pf-m-small" id="btn-refresh">Refresh</button>' +
            '</div>' +
            '<table class="pf-v6-c-table pf-m-compact pf-m-grid-md" id="runs-table">' +
            '<thead><tr>' +
            '<th>Run ID</th><th>Title</th><th>Repo</th><th>Issue</th><th>Phase</th>' +
            '<th>Source</th><th>Tier</th><th>PR</th><th>Created</th><th>Actions</th>' +
            '</tr></thead>' +
            '<tbody id="runs-tbody"><tr><td colspan="10">Loading...</td></tr></tbody>' +
            '</table>' +
            '</section></main></div>';

        document.body.innerHTML = html;
        document.getElementById("btn-refresh").addEventListener("click", loadRuns);
        document.getElementById("filter-repo").addEventListener("input", loadRuns);
        document.getElementById("filter-phase").addEventListener("change", loadRuns);
        loadRuns();
        startAutoRefresh();
    }

    function loadRuns() {
        var repo = document.getElementById("filter-repo").value.trim();
        var phase = document.getElementById("filter-phase").value;
        var params = [];
        if (repo) params.push("repo=" + encodeURIComponent(repo));
        if (phase) params.push("phase=" + encodeURIComponent(phase));
        var path = "/api/runs" + (params.length ? "?" + params.join("&") : "");

        apiGet(path).then(function (data) {
            var tbody = document.getElementById("runs-tbody");
            if (!tbody) return;
            if (!data || data.length === 0) {
                tbody.innerHTML = '<tr><td colspan="10">No runs found</td></tr>';
                return;
            }
            var DELETABLE = ["failed", "cancelled", "shipped"];
            var html = "";
            for (var i = 0; i < data.length; i++) {
                var r = data[i];
                var titleDisplay = r.title || truncate(r.run_id, 30);
                var prLink = r.pr_url
                    ? '<a href="' + escapeHtml(r.pr_url) + '" target="_blank">#' + r.pr_url.split("/").pop() + '</a>'
                    : "";
                var deleteBtn = DELETABLE.indexOf(r.phase) !== -1
                    ? '<button class="pf-v6-c-button pf-m-danger pf-m-small ashigaru-delete-btn"' +
                      ' data-run-id="' + escapeHtml(r.run_id) + '">Delete</button>'
                    : "";
                html += '<tr class="pf-v6-c-table__tr ashigaru-run-row" data-run-id="' + escapeHtml(r.run_id) + '">' +
                    '<td class="pf-v6-c-table__td"><code>' + escapeHtml(r.run_id) + '</code></td>' +
                    '<td class="pf-v6-c-table__td ashigaru-title-cell">' + escapeHtml(titleDisplay) + '</td>' +
                    '<td class="pf-v6-c-table__td">' + escapeHtml(r.repo || "") + '</td>' +
                    '<td class="pf-v6-c-table__td">#' + (r.issue || "") + '</td>' +
                    '<td class="pf-v6-c-table__td">' + phaseBadge(r.phase) + '</td>' +
                    '<td class="pf-v6-c-table__td">' + sourceBadge(r.source) + '</td>' +
                    '<td class="pf-v6-c-table__td">' + (r.current_tier || "") + '</td>' +
                    '<td class="pf-v6-c-table__td">' + prLink + '</td>' +
                    '<td class="pf-v6-c-table__td">' + formatTime(r.created) + '</td>' +
                    '<td class="pf-v6-c-table__td">' + deleteBtn + '</td>' +
                    '</tr>';
            }
            tbody.innerHTML = html;
            var rows = tbody.querySelectorAll(".ashigaru-run-row");
            for (var j = 0; j < rows.length; j++) {
                rows[j].addEventListener("click", function () {
                    stopAutoRefresh();
                    renderDetailPage(this.dataset.runId);
                });
            }
            var delBtns = tbody.querySelectorAll(".ashigaru-delete-btn");
            for (var k = 0; k < delBtns.length; k++) {
                delBtns[k].addEventListener("click", function (evt) {
                    evt.stopPropagation();
                    var rid = this.dataset.runId;
                    if (!confirm("Delete run " + rid + "? This cannot be undone.")) return;
                    apiDelete("/api/runs/" + encodeURIComponent(rid))
                        .then(function () { loadRuns(); })
                        .catch(function (err) { alert("Delete failed: " + String(err)); });
                });
            }
        }).catch(function (err) {
            var tbody = document.getElementById("runs-tbody");
            if (tbody) tbody.innerHTML = '<tr><td colspan="10">Error: ' + escapeHtml(String(err)) + '</td></tr>';
        });
    }

    function startAutoRefresh() {
        stopAutoRefresh();
        refreshTimer = setInterval(loadRuns, 15000);
    }

    function stopAutoRefresh() {
        if (refreshTimer) {
            clearInterval(refreshTimer);
            refreshTimer = null;
        }
    }

    // ---- Run Detail View -----------------------------------------------------

    function renderDetailPage(runId) {
        document.body.innerHTML = '<div class="pf-v6-c-page"><main class="pf-v6-c-page__main">' +
            '<section class="pf-v6-c-page__main-section">' +
            '<div class="ashigaru-back-btn" id="btn-back">&larr; Back to runs</div>' +
            '<div id="detail-header"><p>Loading...</p></div>' +
            '</section>' +
            '<section class="pf-v6-c-page__main-section pf-m-light" id="detail-content">' +
            '</section></main></div>';

        document.getElementById("btn-back").addEventListener("click", renderListPage);

        Promise.all([
            apiGet("/api/runs/" + encodeURIComponent(runId)),
            apiGet("/api/runs/" + encodeURIComponent(runId) + "/activity?tail=50"),
            apiGetText("/api/runs/" + encodeURIComponent(runId) + "/log")
        ]).then(function (results) {
            renderDetailContent(results[0], results[1], results[2]);
        }).catch(function (err) {
            var el = document.getElementById("detail-content");
            if (el) el.innerHTML = '<p>Error: ' + escapeHtml(String(err)) + '</p>';
        });
    }

    function renderDetailContent(detail, activity, log) {
        var header = document.getElementById("detail-header");
        if (header) {
            var title = detail.title || detail.run_id;
            header.innerHTML = '<div class="pf-v6-c-content">' +
                '<h1>' + escapeHtml(title) + '</h1>' +
                '<p>' + escapeHtml(detail.repo) + ' #' + detail.issue +
                ' &mdash; ' + phaseBadge(detail.phase) + ' ' + sourceBadge(detail.source) + '</p>' +
                '</div>';
        }

        var el = document.getElementById("detail-content");
        if (!el) return;

        var html = '<div class="pf-v6-l-grid pf-m-gutter">';

        // -- Metadata card --
        html += '<div class="pf-v6-l-grid__item pf-m-12-col pf-m-6-col-on-lg">' +
            '<div class="pf-v6-c-card"><div class="pf-v6-c-card__header"><div class="pf-v6-c-card__header-main">Metadata</div></div>' +
            '<div class="pf-v6-c-card__body"><dl class="pf-v6-c-description-list pf-m-horizontal">';

        var fields = [
            ["Run ID", '<code>' + escapeHtml(detail.run_id) + '</code>'],
            ["Branch", detail.branch],
            ["Model", detail.model],
            ["Tier", detail.current_tier],
            ["Created", formatTime(detail.created)],
            ["Updated", formatTime(detail.updated)],
            ["Events", detail.event_count],
            ["PR", detail.pr_url ? '<a href="' + escapeHtml(detail.pr_url) + '" target="_blank">' + escapeHtml(detail.pr_url) + '</a>' : "none"],
            ["Preview", detail.preview_url ? '<a href="' + escapeHtml(detail.preview_url) + '" target="_blank">' + escapeHtml(detail.preview_url) + '</a>' : "none"]
        ];
        if (detail.failure_reason) {
            fields.push(["Failure", '<span style="color:#c9190b">' + escapeHtml(detail.failure_reason) + '</span>']);
        }

        for (var i = 0; i < fields.length; i++) {
            html += '<div class="pf-v6-c-description-list__group">' +
                '<dt class="pf-v6-c-description-list__term">' + fields[i][0] + '</dt>' +
                '<dd class="pf-v6-c-description-list__description">' + (fields[i][1] || "") + '</dd></div>';
        }
        html += '</dl></div></div></div>';

        // -- Attempts card --
        var attempts = detail.attempts || [];
        if (attempts.length > 0) {
            html += '<div class="pf-v6-l-grid__item pf-m-12-col pf-m-6-col-on-lg">' +
                '<div class="pf-v6-c-card"><div class="pf-v6-c-card__header"><div class="pf-v6-c-card__header-main">Attempts (' + attempts.length + ')</div></div>' +
                '<div class="pf-v6-c-card__body"><table class="pf-v6-c-table pf-m-compact"><thead><tr>' +
                '<th>Tier</th><th>Model</th><th>Gate</th><th>Output</th>' +
                '</tr></thead><tbody>';
            for (var a = 0; a < attempts.length; a++) {
                var att = attempts[a];
                var gateCls = att.gate === "passed" ? "ashigaru-attempt-passed" : "ashigaru-attempt-failed";
                html += '<tr><td>' + (att.tier || "") + '</td>' +
                    '<td><code>' + escapeHtml(att.model || "") + '</code></td>' +
                    '<td class="' + gateCls + '">' + (att.gate || "") + '</td>' +
                    '<td>' + escapeHtml(truncate(att.gate_output || "", 80)) + '</td></tr>';
            }
            html += '</tbody></table></div></div></div>';
        }

        // -- Activity timeline card --
        html += '<div class="pf-v6-l-grid__item pf-m-12-col">' +
            '<div class="pf-v6-c-card"><div class="pf-v6-c-card__header"><div class="pf-v6-c-card__header-main">Activity Timeline (' + (activity ? activity.length : 0) + ')</div></div>' +
            '<div class="pf-v6-c-card__body">';

        if (activity && activity.length > 0) {
            html += '<table class="pf-v6-c-table pf-m-compact"><thead><tr>' +
                '<th>Time</th><th>Kind</th><th>Summary</th><th>Files</th>' +
                '</tr></thead><tbody>';
            for (var t = 0; t < activity.length; t++) {
                var act = activity[t];
                var filesStr = (act.files || []).map(function (f) { return escapeHtml(f); }).join(", ");
                html += '<tr>' +
                    '<td>' + formatTimeShort(act.timestamp) + '</td>' +
                    '<td><span class="ashigaru-activity-kind">' + escapeHtml(act.kind || "") + '</span></td>' +
                    '<td>' + escapeHtml(act.summary || "") + '</td>' +
                    '<td class="ashigaru-event-preview">' + filesStr + '</td>' +
                    '</tr>';
            }
            html += '</tbody></table>';
        } else {
            html += '<p>No activity recorded</p>';
        }
        html += '</div></div></div>';

        // -- Brief card --
        if (detail.brief) {
            html += '<div class="pf-v6-l-grid__item pf-m-12-col">' +
                '<div class="pf-v6-c-card"><div class="pf-v6-c-card__header"><div class="pf-v6-c-card__header-main">Brief</div></div>' +
                '<div class="pf-v6-c-card__body"><pre class="ashigaru-log-pre">' + escapeHtml(detail.brief) + '</pre></div></div></div>';
        }

        // -- Log card --
        if (log && log.trim()) {
            html += '<div class="pf-v6-l-grid__item pf-m-12-col">' +
                '<div class="pf-v6-c-card"><div class="pf-v6-c-card__header"><div class="pf-v6-c-card__header-main">Combined Log</div></div>' +
                '<div class="pf-v6-c-card__body"><pre class="ashigaru-log-pre">' + escapeHtml(log) + '</pre></div></div></div>';
        }

        html += '</div>';
        el.innerHTML = html;
    }

    // ---- Init ----------------------------------------------------------------

    function init() {
        renderListPage();
    }

    if (document.readyState === "loading")
        document.addEventListener("DOMContentLoaded", init);
    else
        init();
})();
