// DNS AI Monitoring Platform - Frontend
const API = {
    get: (url) => fetch(url).then(r => r.json()),
    post: (url) => fetch(url, { method: 'POST' }).then(r => r.json()),
};

let charts = {};
let currentAgentResults = {};
let autoRefreshInterval = null;
let llmMode = 'manual'; // 'manual' or 'auto'

// --- Navigation ---
document.querySelectorAll('.nav-links li').forEach(li => {
    li.addEventListener('click', () => {
        document.querySelectorAll('.nav-links li').forEach(l => l.classList.remove('active'));
        li.classList.add('active');
        const panel = li.dataset.panel;
        document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
        document.getElementById(`panel-${panel}`).classList.add('active');
        document.getElementById('page-title').textContent = li.textContent.trim();
    });
});

// --- Buttons ---
document.getElementById('btn-collect').addEventListener('click', async () => {
    const btn = document.getElementById('btn-collect');
    btn.disabled = true;
    btn.textContent = 'Collecting...';
    try {
        await API.post('/api/dns/collect');
        await refreshAll();
    } finally {
        btn.disabled = false;
        btn.textContent = 'Collect DNS';
    }
});

document.getElementById('btn-run-agents').addEventListener('click', async () => {
    const btn = document.getElementById('btn-run-agents');
    btn.disabled = true;
    btn.textContent = 'Running Analysis...';
    try {
        const results = await API.post('/api/agents/run-all');
        if (results.error) {
            console.warn('Agent run:', results.error);
        } else {
            currentAgentResults = results.results || results;
            updateAgentResults();
            updateOverviewFromResults();
        }
    } catch (e) {
        console.error('Agent run failed:', e);
    } finally {
        btn.disabled = false;
        btn.textContent = 'Run Analysis';
    }
});

// --- LLM Mode Toggle ---
document.getElementById('llm-auto-toggle').addEventListener('change', async (e) => {
    const newMode = e.target.checked ? 'auto' : 'manual';
    try {
        const result = await API.post(`/api/llm/mode/${newMode}`);
        llmMode = result.mode;
        updateModeLabel();
    } catch (err) {
        console.error('Failed to set LLM mode:', err);
        e.target.checked = !e.target.checked; // revert
    }
});

function updateModeLabel() {
    const label = document.getElementById('llm-mode-label');
    const toggle = document.getElementById('llm-auto-toggle');
    if (llmMode === 'auto') {
        label.textContent = 'Auto';
        label.classList.add('auto-on');
        toggle.checked = true;
    } else {
        label.textContent = 'Manual';
        label.classList.remove('auto-on');
        toggle.checked = false;
    }
}

async function loadLLMMode() {
    try {
        const result = await API.get('/api/llm/mode');
        llmMode = result.mode;
        updateModeLabel();
    } catch (e) { console.error(e); }
}

// --- Role Tabs ---
document.querySelectorAll('.role-tab').forEach(tab => {
    tab.addEventListener('click', () => {
        document.querySelectorAll('.role-tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        showRoleView(tab.dataset.role);
    });
});

function showRoleView(role) {
    const el = document.getElementById('role-view-content');
    const dashboard = currentAgentResults.dashboard;
    // Prefer LLM-generated role views, fall back to DNS-computed views
    const views = (dashboard && dashboard.role_views) ? dashboard.role_views : _dnsRoleViews;
    if (!views) {
        el.innerHTML = 'Collecting DNS data...';
        return;
    }
    const view = views[role];
    if (!view) {
        el.innerHTML = `No data for ${role} view.`;
        return;
    }
    let html = `<strong>Status:</strong> ${view.status || 'N/A'}<br><br>`;
    if (view.key_metrics && view.key_metrics.length) {
        html += '<strong>Key Metrics:</strong><ul>' + view.key_metrics.map(m => `<li>${m}</li>`).join('') + '</ul>';
    }
    if (view.action_items && view.action_items.length) {
        html += '<strong>Action Items:</strong><ul>' + view.action_items.map(a => `<li>${a}</li>`).join('') + '</ul>';
    }
    el.innerHTML = html;
}

// --- Data Refresh ---
async function refreshAll() {
    await Promise.all([refreshStatus(), refreshDNSSummary(), refreshDNSHistory(), refreshAgentResults()]);
}

async function refreshStatus() {
    try {
        const status = await API.get('/api/status');
        const el = document.getElementById('system-status');
        el.textContent = status.is_running ? 'System: Analyzing...' : `System: Active (${status.dns_summary.total_queries || 0} queries)`;
        el.className = 'status-indicator' + (status.is_running ? ' warning' : '');
    } catch (e) { console.error(e); }
}

// Store latest DNS summary globally for overview computations
let latestDNSSummary = null;

async function refreshDNSSummary() {
    try {
        const summary = await API.get('/api/dns/summary');
        latestDNSSummary = summary;
        document.getElementById('kpi-avg-ms').textContent = summary.avg_response_ms || '--';
        document.getElementById('kpi-success').textContent = (summary.success_rate || '--') + '%';
        document.getElementById('kpi-total').textContent = summary.total_queries || 0;

        const avgMs = summary.avg_response_ms || 0;
        const avgEl = document.getElementById('kpi-avg-ms');
        avgEl.className = 'kpi-value' + (avgMs < 50 ? ' good' : avgMs < 200 ? '' : avgMs < 500 ? ' warning' : ' critical');

        const sr = summary.success_rate || 0;
        const srEl = document.getElementById('kpi-success');
        srEl.className = 'kpi-value' + (sr >= 95 ? ' good' : sr >= 80 ? ' warning' : ' critical');

        updateServerChart(summary.server_avg_ms || {});
        updateStatusChart(summary.status_counts || {});

        // Compute overview from DNS data (no LLM needed)
        updateOverviewFromDNS(summary);
    } catch (e) { console.error(e); }
}

function updateOverviewFromDNS(summary) {
    const sr = summary.success_rate || 0;
    const avgMs = summary.avg_response_ms || 0;
    const total = summary.total_queries || 0;
    const statusCounts = summary.status_counts || {};
    const serverAvg = summary.server_avg_ms || {};

    // --- Health Score (computed from DNS data) ---
    // Only set if LLM hasn't already provided a richer value
    const dashboard = currentAgentResults.dashboard;
    if (!dashboard || !dashboard.health_score) {
        // Health = weighted: 60% success rate + 25% latency score + 15% data availability
        const latencyScore = avgMs < 30 ? 100 : avgMs < 100 ? 85 : avgMs < 300 ? 60 : avgMs < 500 ? 40 : 20;
        const dataScore = total > 50 ? 100 : total > 20 ? 70 : total > 0 ? 40 : 0;
        const health = Math.round(sr * 0.6 + latencyScore * 0.25 + dataScore * 0.15);
        const healthEl = document.getElementById('kpi-health');
        healthEl.textContent = health;
        healthEl.className = 'kpi-value' + (health >= 80 ? ' good' : health >= 50 ? ' warning' : ' critical');
        document.getElementById('kpi-health-status').textContent =
            health >= 90 ? 'Healthy' : health >= 70 ? 'Degraded' : health >= 50 ? 'Warning' : 'Critical';
    }

    // --- Executive Summary (computed from DNS data) ---
    if (!dashboard || !dashboard.executive_summary) {
        const serverList = Object.entries(serverAvg);
        const slowServers = serverList.filter(([, ms]) => ms > 100).map(([s, ms]) => `${s} (${ms}ms)`);
        const errorTypes = Object.entries(statusCounts).filter(([k]) => k !== 'success');
        let summaryParts = [];
        summaryParts.push(`Monitoring ${serverList.length} DNS servers with ${total} queries collected.`);
        summaryParts.push(`Success rate: ${sr}%, average response: ${avgMs}ms.`);
        if (slowServers.length) summaryParts.push(`Slow servers: ${slowServers.join(', ')}.`);
        if (errorTypes.length) summaryParts.push(`Issues: ${errorTypes.map(([k, v]) => `${v} ${k}`).join(', ')}.`);
        if (sr >= 95 && avgMs < 100) summaryParts.push('All systems operating normally.');
        document.getElementById('executive-summary').textContent = summaryParts.join(' ');
    }

    // --- Active Alerts (computed from DNS data) ---
    if (!dashboard || !dashboard.active_alerts || !dashboard.active_alerts.length) {
        const alertsEl = document.getElementById('active-alerts');
        const alerts = [];
        // High latency alerts
        for (const [server, ms] of Object.entries(serverAvg)) {
            if (ms > 500) alerts.push({ severity: 'critical', message: `${server}: extremely high latency (${ms}ms)` });
            else if (ms > 200) alerts.push({ severity: 'warning', message: `${server}: elevated latency (${ms}ms)` });
        }
        // Error rate alert
        if (sr < 80) alerts.push({ severity: 'critical', message: `Low success rate: ${sr}% (threshold: 80%)` });
        else if (sr < 95) alerts.push({ severity: 'warning', message: `Success rate below target: ${sr}% (target: 95%)` });
        // Specific error types
        if (statusCounts.timeout) alerts.push({ severity: 'warning', message: `${statusCounts.timeout} timeout(s) detected` });
        if (statusCounts.servfail) alerts.push({ severity: 'critical', message: `${statusCounts.servfail} SERVFAIL response(s)` });
        if (statusCounts.noanswer) alerts.push({ severity: 'info', message: `${statusCounts.noanswer} NoAnswer response(s) - possible missing records` });

        if (alerts.length) {
            alertsEl.innerHTML = alerts.map(a =>
                `<div class="alert-item ${a.severity}"><span class="alert-badge ${a.severity}">${a.severity.toUpperCase()}</span>${a.message}</div>`
            ).join('');
        } else {
            alertsEl.innerHTML = '<div class="alert-item info"><span class="alert-badge info">OK</span>All DNS servers operating normally</div>';
        }
    }

    // --- Role Views (computed from DNS data) ---
    if (!dashboard || !dashboard.role_views) {
        _dnsRoleViews = buildDNSRoleViews(summary);
    }
}

// Fallback role views from DNS data
let _dnsRoleViews = null;

function buildDNSRoleViews(summary) {
    const sr = summary.success_rate || 0;
    const avgMs = summary.avg_response_ms || 0;
    const serverAvg = summary.server_avg_ms || {};
    const statusCounts = summary.status_counts || {};
    const total = summary.total_queries || 0;
    const servers = Object.entries(serverAvg);
    const slowServers = servers.filter(([, ms]) => ms > 100);
    const fastestServer = servers.length ? servers.reduce((a, b) => a[1] < b[1] ? a : b) : null;
    const slowestServer = servers.length ? servers.reduce((a, b) => a[1] > b[1] ? a : b) : null;

    return {
        ops: {
            status: sr >= 95 ? 'Operational' : sr >= 80 ? 'Degraded' : 'Impaired',
            key_metrics: [
                `Success Rate: ${sr}%`,
                `Avg Response: ${avgMs}ms`,
                `Total Queries: ${total}`,
                `Servers Monitored: ${servers.length}`,
                fastestServer ? `Fastest: ${fastestServer[0]} (${fastestServer[1]}ms)` : null,
                slowestServer ? `Slowest: ${slowestServer[0]} (${slowestServer[1]}ms)` : null,
            ].filter(Boolean),
            action_items: [
                ...(slowServers.length ? [`Investigate ${slowServers.length} slow server(s): ${slowServers.map(([s]) => s).join(', ')}`] : []),
                ...(sr < 95 ? [`Success rate ${sr}% below 95% SLA target`] : []),
                ...(statusCounts.timeout ? [`${statusCounts.timeout} timeout(s) need investigation`] : []),
                ...(!slowServers.length && sr >= 95 ? ['No action items - all systems nominal'] : []),
            ],
        },
        network: {
            status: avgMs < 100 ? 'Normal Latency' : avgMs < 300 ? 'Elevated Latency' : 'High Latency',
            key_metrics: [
                `Avg Latency: ${avgMs}ms`,
                ...servers.map(([s, ms]) => `${s}: ${ms}ms`),
            ],
            action_items: [
                ...(slowServers.map(([s, ms]) => `${s}: ${ms}ms - check network path`)),
                ...(statusCounts.timeout ? [`Timeouts detected - check network connectivity`] : []),
                ...(!slowServers.length ? ['Network performance within normal parameters'] : []),
            ],
        },
        security: {
            status: statusCounts.servfail || statusCounts.nxdomain > 5 ? 'Review Needed' : 'No Threats Detected',
            key_metrics: [
                `Total Queries: ${total}`,
                `NXDOMAIN: ${statusCounts.nxdomain || 0}`,
                `SERVFAIL: ${statusCounts.servfail || 0}`,
                `NoAnswer: ${statusCounts.noanswer || 0}`,
            ],
            action_items: [
                ...(statusCounts.servfail ? [`${statusCounts.servfail} SERVFAIL - check for DNS poisoning or misconfiguration`] : []),
                ...(statusCounts.nxdomain > 5 ? [`High NXDOMAIN count (${statusCounts.nxdomain}) - possible DGA activity`] : []),
                ...(!statusCounts.servfail && !(statusCounts.nxdomain > 5) ? ['No security concerns detected'] : []),
            ],
        },
        leadership: {
            status: sr >= 95 ? 'Green' : sr >= 80 ? 'Yellow' : 'Red',
            key_metrics: [
                `SLA Compliance: ${sr >= 99.9 ? 'Met (99.9%)' : sr >= 95 ? 'At Risk' : 'Breached'}`,
                `Availability: ${sr}%`,
                `Performance: ${avgMs < 100 ? 'Good' : avgMs < 300 ? 'Acceptable' : 'Poor'} (${avgMs}ms avg)`,
                `Infrastructure: ${servers.length} servers active`,
            ],
            action_items: [
                ...(sr < 95 ? [`Availability at ${sr}% - below 95% target, escalation recommended`] : []),
                ...(avgMs > 300 ? [`Response time ${avgMs}ms exceeds acceptable threshold`] : []),
                ...(sr >= 95 && avgMs <= 300 ? ['All KPIs within acceptable ranges'] : []),
            ],
        },
    };
}

async function refreshDNSHistory() {
    try {
        const history = await API.get('/api/dns/history?limit=100');
        updateQueryTable(history);
        updateResponseTrendChart(history);
    } catch (e) { console.error(e); }
}

async function refreshAgentResults() {
    try {
        const results = await API.get('/api/agents/results');
        if (Object.keys(results).length > 0) {
            currentAgentResults = results;
            updateAgentResults();
            updateOverviewFromResults();
        }
    } catch (e) { console.error(e); }
}

// --- Charts ---
function updateServerChart(serverAvg) {
    const ctx = document.getElementById('chart-server-response');
    if (!ctx) return;
    const labels = Object.keys(serverAvg);
    const data = Object.values(serverAvg);
    if (charts.serverResponse) charts.serverResponse.destroy();
    charts.serverResponse = new Chart(ctx, {
        type: 'bar',
        data: {
            labels,
            datasets: [{
                label: 'Avg Response (ms)',
                data,
                backgroundColor: data.map(v => v < 50 ? '#059669' : v < 200 ? '#2563eb' : v < 500 ? '#d97706' : '#dc2626'),
                borderRadius: 6,
            }]
        },
        options: {
            responsive: true,
            plugins: { legend: { display: false } },
            scales: { y: { beginAtZero: true, title: { display: true, text: 'ms' } } }
        }
    });

    // Also update server perf chart
    const ctx2 = document.getElementById('chart-server-perf');
    if (ctx2) {
        if (charts.serverPerf) charts.serverPerf.destroy();
        charts.serverPerf = new Chart(ctx2, {
            type: 'radar',
            data: {
                labels,
                datasets: [{
                    label: 'Response Time (ms)',
                    data,
                    backgroundColor: 'rgba(37, 99, 235, 0.15)',
                    borderColor: '#2563eb',
                    pointBackgroundColor: '#2563eb',
                }]
            },
            options: { responsive: true, scales: { r: { beginAtZero: true } } }
        });
    }
}

function updateStatusChart(statusCounts) {
    const ctx = document.getElementById('chart-status-dist');
    if (!ctx) return;
    const labels = Object.keys(statusCounts);
    const data = Object.values(statusCounts);
    const colors = { success: '#059669', nxdomain: '#d97706', timeout: '#dc2626', error: '#dc2626', noanswer: '#6366f1', servfail: '#e11d48' };
    if (charts.statusDist) charts.statusDist.destroy();
    charts.statusDist = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels,
            datasets: [{ data, backgroundColor: labels.map(l => colors[l] || '#94a3b8') }]
        },
        options: { responsive: true, plugins: { legend: { position: 'bottom' } } }
    });
}

function updateResponseTrendChart(history) {
    const ctx = document.getElementById('chart-response-trend');
    if (!ctx) return;
    const successful = history.filter(h => h.status === 'success').slice(-50);
    const labels = successful.map((_, i) => i + 1);
    const data = successful.map(h => h.response_time_ms);
    if (charts.responseTrend) charts.responseTrend.destroy();
    charts.responseTrend = new Chart(ctx, {
        type: 'line',
        data: {
            labels,
            datasets: [{
                label: 'Response Time (ms)',
                data,
                borderColor: '#2563eb',
                backgroundColor: 'rgba(37,99,235,0.08)',
                fill: true,
                tension: 0.3,
                pointRadius: 2,
            }]
        },
        options: {
            responsive: true,
            plugins: { legend: { display: false } },
            scales: { y: { beginAtZero: true, title: { display: true, text: 'ms' } } }
        }
    });
}

// --- Table ---
function updateQueryTable(history) {
    const tbody = document.getElementById('dns-query-tbody');
    if (!tbody) return;
    const rows = history.slice(-100).reverse();
    tbody.innerHTML = rows.map(r => {
        const time = new Date(r.timestamp * 1000).toLocaleTimeString();
        return `<tr>
            <td>${time}</td>
            <td>${r.server}</td>
            <td>${r.domain}</td>
            <td>${r.query_type}</td>
            <td>${r.response_time_ms}</td>
            <td><span class="status-badge ${r.status}">${r.status}</span></td>
        </tr>`;
    }).join('');
}

// --- Agent Results ---
function updateAgentResults() {
    const grid = document.getElementById('agents-grid');
    if (!grid) return;

    const agentNames = {
        experience: 'DNS Experience Agent',
        request_handling: 'Request Handling Agent',
        l2: 'DNS L2 Agent',
        anomaly: 'Anomaly Detection Agent',
        failure_prediction: 'Failure Prediction Agent',
        misconfiguration: 'Misconfiguration Agent',
        query_log: 'Query Log Analytics Agent',
        client_scoring: 'Client Experience Scoring',
        dashboard: 'Dashboard Agent',
    };

    grid.innerHTML = Object.entries(agentNames).map(([key, name]) => {
        const result = currentAgentResults[key];
        const hasResult = result && !result.error && !result.raw_analysis?.startsWith('[LLM Error');
        const hasError = result && (result.error || result.raw_analysis?.startsWith('[LLM Error'));
        const alertCount = (result && result.alerts) ? result.alerts.length : 0;
        const remCount = (result && result.remediation_actions) ? result.remediation_actions.length : 0;
        const dotClass = hasError ? 'error' : hasResult ? 'active' : '';
        const status = hasError ? 'Error' : hasResult ? `Results${alertCount ? ` (${alertCount} alerts)` : ''}` : 'No data';
        return `<div class="agent-card" data-agent="${key}">
            <div class="agent-name">${name}</div>
            <div class="agent-status"><span class="dot ${dotClass}"></span>${status}</div>
            ${remCount ? `<div class="agent-status" style="font-size:11px; color:var(--accent);">${remCount} remediation actions</div>` : ''}
        </div>`;
    }).join('');

    grid.querySelectorAll('.agent-card').forEach(card => {
        card.addEventListener('click', () => {
            grid.querySelectorAll('.agent-card').forEach(c => c.classList.remove('selected'));
            card.classList.add('selected');
            showAgentDetail(card.dataset.agent);
        });
    });
}

function showAgentDetail(agentKey) {
    const el = document.getElementById('agent-detail');
    const result = currentAgentResults[agentKey];
    if (!result) {
        el.textContent = 'No results for this agent. Run analysis first.';
        return;
    }
    if (result.error) {
        el.innerHTML = `<div class="alert-item critical">Error: ${result.error}</div>`;
        return;
    }
    // Pretty print the result
    const display = { ...result };
    delete display.agent;
    delete display.timestamp;
    el.textContent = JSON.stringify(display, null, 2);
}

// --- Overview from Agent Results ---
function updateOverviewFromResults() {
    const dashboard = currentAgentResults.dashboard;
    const hasLLM = dashboard && !dashboard.raw_analysis?.startsWith('[LLM Error') && dashboard.health_score;

    if (hasLLM) {
        // LLM-enriched dashboard overrides DNS-computed values
        const healthScore = dashboard.health_score;
        const healthEl = document.getElementById('kpi-health');
        healthEl.textContent = healthScore;
        healthEl.className = 'kpi-value' + (healthScore >= 80 ? ' good' : healthScore >= 50 ? ' warning' : ' critical');
        document.getElementById('kpi-health-status').textContent = dashboard.overall_health || 'Unknown';
        document.getElementById('executive-summary').textContent = dashboard.executive_summary || dashboard.summary || '';

        // LLM alerts
        const alertsEl = document.getElementById('active-alerts');
        if (dashboard.active_alerts && dashboard.active_alerts.length) {
            alertsEl.innerHTML = dashboard.active_alerts.map(a =>
                `<div class="alert-item ${a.severity}"><span class="alert-badge ${a.severity}">${a.severity.toUpperCase()}</span>${a.message}</div>`
            ).join('');
        }

        // LLM role views
        showRoleView('ops');
    } else if (latestDNSSummary) {
        // Re-apply DNS-computed overview (already set by refreshDNSSummary, but refresh role view)
        showRoleView('ops');
    }

    // Analytics scores from agents (if available)
    const exp = currentAgentResults.experience;
    if (exp && !exp.raw_analysis?.startsWith('[LLM Error')) {
        document.getElementById('analytics-experience').textContent = exp.overall_score || '--';
    }
    const cx = currentAgentResults.client_scoring;
    if (cx && !cx.raw_analysis?.startsWith('[LLM Error')) {
        document.getElementById('analytics-cx').textContent = cx.overall_cx_score || '--';
    }
    const anomaly = currentAgentResults.anomaly;
    if (anomaly && !anomaly.raw_analysis?.startsWith('[LLM Error')) {
        document.getElementById('analytics-anomalies').textContent = anomaly.anomalies_detected || 0;
    }

    // Insights
    updateInsights();

    // Orchestrator narrative
    updateOrchestratorNarrative();

    // Remediation tickets
    loadRemediation();
}

function updateInsights() {
    const el = document.getElementById('analytics-insights');
    if (!el) return;
    const insights = [];
    for (const [key, result] of Object.entries(currentAgentResults)) {
        if (key.startsWith('_')) continue;
        if (result && result.summary) {
            insights.push({ agent: result.agent || key, summary: result.summary });
        }
    }
    if (insights.length === 0) {
        el.innerHTML = 'Run agents to see AI insights...';
        return;
    }
    el.innerHTML = insights.map(i =>
        `<div class="insight-item"><strong>${i.agent}:</strong> ${i.summary}</div>`
    ).join('');
}

// --- Orchestrator Narrative ---
function updateOrchestratorNarrative() {
    const orch = currentAgentResults._orchestrator;
    if (!orch) return;

    const narrativeEl = document.getElementById('orchestrator-narrative');
    if (narrativeEl) {
        narrativeEl.textContent = orch.narrative || orch.root_cause_narrative || 'No narrative generated.';
    }

    // Root cause on remediation panel
    const rcEl = document.getElementById('rem-root-cause');
    if (rcEl && orch.root_cause_narrative) {
        rcEl.textContent = orch.root_cause_narrative;
    }

    // Correlated insights
    const insEl = document.getElementById('rem-insights');
    if (insEl && orch.correlated_insights && orch.correlated_insights.length) {
        insEl.innerHTML = orch.correlated_insights.map(i =>
            `<div class="insight-item">${typeof i === 'string' ? i : i.insight || JSON.stringify(i)}</div>`
        ).join('');
    }
}

// --- Remediation ---
async function loadRemediation() {
    try {
        const [tickets, stats] = await Promise.all([
            API.get('/api/remediation/tickets'),
            API.get('/api/remediation/stats'),
        ]);

        // KPIs
        document.getElementById('rem-total').textContent = stats.total_tickets || 0;
        document.getElementById('rem-incidents').textContent = (stats.by_category || {}).incident || 0;
        document.getElementById('rem-changes').textContent = (stats.by_category || {}).change || 0;
        const active = ((stats.by_status || {}).ticket_created || 0) + ((stats.by_status || {}).in_progress || 0);
        document.getElementById('rem-active').textContent = active;

        // Auto-remediation status
        try {
            const config = await API.get('/api/config');
            const autoEl = document.getElementById('rem-auto-status');
            const autoSub = document.getElementById('rem-auto-sub');
            if (config.auto_remediation) {
                autoEl.textContent = 'Enabled';
                autoEl.style.color = 'var(--accent)';
                const resolved = (stats.by_status || {}).resolved || 0;
                autoSub.textContent = `${resolved} auto-resolved`;
            } else {
                autoEl.textContent = 'Disabled';
                autoEl.style.color = 'var(--text-secondary)';
                autoSub.textContent = 'enable in config';
            }
        } catch (_) {}

        // Table
        const tbody = document.getElementById('remediation-tbody');
        if (tickets && tickets.length) {
            tbody.innerHTML = tickets.reverse().map(t => {
                const priorityClass = t.priority === 'critical' ? 'critical' : t.priority === 'high' ? 'warning' : 'info';
                return `<tr>
                    <td>${t.ticket_id}</td>
                    <td>${t.snow_number || 'Pending'}</td>
                    <td>${t.source_agent}</td>
                    <td style="max-width:250px; overflow:hidden; text-overflow:ellipsis;">${t.action}</td>
                    <td><span class="alert-badge ${priorityClass}">${t.priority}</span></td>
                    <td>${t.itsm_category}</td>
                    <td>${t.target}</td>
                    <td><span class="status-badge ${t.status}">${t.status.replace(/_/g, ' ')}</span></td>
                </tr>`;
            }).join('');
        } else {
            tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; color:var(--text-secondary);">No remediation tickets yet. Run agents to detect issues.</td></tr>';
        }
    } catch (e) {
        console.error('Remediation load error:', e);
    }
}

// --- Local DNS Server ---
async function loadLocalDNS() {
    try {
        const data = await API.get('/api/local-dns/status');
        document.getElementById('local-dns-host').textContent = data.host || '--';

        if (data.reachable) {
            const el = document.getElementById('local-dns-status-badge');
            el.textContent = data.bind_running ? 'Running' : 'Reachable';
            el.className = 'kpi-value good';
        } else {
            const el = document.getElementById('local-dns-status-badge');
            el.textContent = 'Unreachable';
            el.className = 'kpi-value critical';
        }

        document.getElementById('local-dns-version').textContent = data.version || 'Unknown version';
        document.getElementById('local-dns-zone-count').textContent = (data.zones || []).length;

        // Populate zone selector
        const select = document.getElementById('local-dns-zone-select');
        const currentVal = select.value;
        select.innerHTML = '<option value="">Select a zone...</option>';
        (data.zones || []).forEach(z => {
            const opt = document.createElement('option');
            opt.value = z.name;
            opt.textContent = `${z.name} (${z.record_count} lines)`;
            select.appendChild(opt);
        });
        if (currentVal) select.value = currentVal;
    } catch (e) {
        document.getElementById('local-dns-status-badge').textContent = 'Error';
        console.error(e);
    }
}

async function setupLocalZones() {
    const btn = document.getElementById('btn-setup-zones');
    btn.disabled = true;
    btn.textContent = 'Setting up...';
    const resultEl = document.getElementById('local-dns-action-result');
    try {
        const results = await API.post('/api/local-dns/setup-zones');
        resultEl.innerHTML = results.map(r =>
            `<div class="alert-item ${r.action === 'error' ? 'critical' : 'info'}">
                <span class="alert-badge ${r.action === 'error' ? 'critical' : 'info'}">${r.action}</span>
                ${r.zone}: ${r.details.join('; ')}
            </div>`
        ).join('');
        await loadLocalDNS();
    } catch (e) {
        resultEl.innerHTML = `<div class="alert-item critical">Setup failed: ${e}</div>`;
    }
    btn.disabled = false;
    btn.textContent = 'Setup Authoritative Zones';
}

async function loadZoneRecords() {
    const zone = document.getElementById('local-dns-zone-select').value;
    const tbody = document.getElementById('local-dns-records-tbody');
    if (!zone) { tbody.innerHTML = ''; return; }
    try {
        const records = await API.get(`/api/local-dns/zones/${zone}/records`);
        tbody.innerHTML = records.map(r =>
            `<tr><td>${r.name}</td><td>${r.type}</td><td>${r.ttl}</td><td>${r.value}</td></tr>`
        ).join('');
        if (!records.length) {
            tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; color:var(--text-secondary);">No records found. Try "Setup Authoritative Zones" first.</td></tr>';
        }
    } catch (e) {
        tbody.innerHTML = `<tr><td colspan="4">Error loading records</td></tr>`;
    }
}

async function queryLocalDNS() {
    const fqdn = document.getElementById('local-dns-query-fqdn').value.trim();
    const rtype = document.getElementById('local-dns-query-type').value;
    const resultEl = document.getElementById('local-dns-query-result');
    if (!fqdn) { resultEl.innerHTML = 'Enter a FQDN to query.'; return; }
    try {
        const data = await API.get(`/api/local-dns/query?fqdn=${encodeURIComponent(fqdn)}&rtype=${rtype}`);
        if (data.exists) {
            resultEl.innerHTML = `
                <div class="alert-item info" style="flex-direction:column; align-items:flex-start;">
                    <strong>${fqdn} (${rtype})</strong> on ${data.server}<br>
                    <strong>Records:</strong> ${data.records.join(', ')}<br>
                    <strong>TTL:</strong> ${data.ttl}s
                </div>`;
        } else {
            resultEl.innerHTML = `<div class="alert-item warning"><span class="alert-badge warning">${data.error || 'Not Found'}</span>${fqdn} (${rtype}) - no records on ${data.server}</div>`;
        }
    } catch (e) {
        resultEl.innerHTML = `<div class="alert-item critical">Query error: ${e}</div>`;
    }
}

// --- Azure ---
async function loadAzure() {
    try {
        const data = await API.get('/api/azure/zones');
        const statusEl = document.getElementById('azure-status');
        const zonesEl = document.getElementById('azure-zones');
        if (!data.enabled) {
            statusEl.innerHTML = '<div class="alert-item info"><span class="alert-badge info">INFO</span>Azure DNS integration is configured but will connect with valid credentials.</div>';
            zonesEl.innerHTML = 'Configure valid Azure credentials in Config.ini to see live zone data.';
            return;
        }
        statusEl.innerHTML = `<strong>Resource Group:</strong> ${data.resource_group}<br><strong>Zones:</strong> ${data.zone_count}`;
        if (data.zones && data.zones.length) {
            zonesEl.innerHTML = data.zones.map(z =>
                `<div class="zone-card"><strong>${z.name}</strong> - Type: ${z.type} - Records: ${z.records}</div>`
            ).join('');
        } else {
            zonesEl.innerHTML = 'No zones found.';
        }
    } catch (e) {
        document.getElementById('azure-status').innerHTML = 'Failed to load Azure DNS info.';
    }
}

// --- Config ---
async function loadConfig() {
    try {
        const cfg = await API.get('/api/config');
        const el = document.getElementById('config-display');
        el.innerHTML = Object.entries(cfg).map(([key, val]) =>
            `<div class="config-item"><div class="label">${key.replace(/_/g, ' ')}</div><div class="value">${val}</div></div>`
        ).join('');
    } catch (e) {
        document.getElementById('config-display').innerHTML = 'Failed to load configuration.';
    }
}

// =============================================================================
// Chat Assistant
// =============================================================================
let pendingAction = null;
const chatSessionId = 'session_' + Date.now();

document.getElementById('btn-chat-send').addEventListener('click', sendChatMessage);
document.getElementById('chat-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChatMessage(); }
});

async function sendChatMessage() {
    const input = document.getElementById('chat-input');
    const msg = input.value.trim();
    if (!msg) return;
    input.value = '';

    appendChatMessage('user', msg);

    // Show typing indicator
    const typingId = appendChatMessage('assistant', '<div class="spinner"></div> Thinking...');

    try {
        const result = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: msg, session_id: chatSessionId }),
        }).then(r => r.json());

        removeChatMessage(typingId);
        handleChatResponse(result);
    } catch (e) {
        removeChatMessage(typingId);
        appendChatMessage('assistant', 'Sorry, an error occurred. Please try again.');
    }
}

function appendChatMessage(role, content) {
    const container = document.getElementById('chat-messages');
    const div = document.createElement('div');
    div.className = `chat-message ${role}`;
    const id = 'msg-' + Date.now();
    div.id = id;
    div.innerHTML = `<div class="chat-bubble">${content}</div>`;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
    return id;
}

function removeChatMessage(id) {
    const el = document.getElementById(id);
    if (el) el.remove();
}

function handleChatResponse(result) {
    const action = result.action;

    if (action === 'add' || action === 'modify' || action === 'delete') {
        // Show confirmation message
        const confirmMsg = result.confirmation_message || `${action.toUpperCase()} ${result.record_name}.${result.zone} (${result.record_type}) -> ${(result.values || []).join(', ')}`;
        appendChatMessage('assistant', confirmMsg + '<br><br><em>Please confirm this action in the panel on the right.</em>');

        // Show pending action in sidebar
        pendingAction = result;
        showPendingAction(result);
    } else {
        // Info/query/status response
        const message = result.message || result.confirmation_message || JSON.stringify(result, null, 2);
        appendChatMessage('assistant', message.replace(/\n/g, '<br>'));
    }
}

function showPendingAction(action) {
    const el = document.getElementById('chat-pending-action');
    const opColors = { add: 'accent', modify: 'primary', delete: 'danger' };
    el.innerHTML = `
        <div class="action-card">
            <div class="action-header" style="color: var(--${opColors[action.action] || 'primary'})">${action.action.toUpperCase()} DNS Record</div>
            <div class="action-detail"><strong>Name:</strong> ${action.record_name}.${action.zone}</div>
            <div class="action-detail"><strong>Type:</strong> ${action.record_type}</div>
            <div class="action-detail"><strong>TTL:</strong> ${action.ttl || 3600}s</div>
            ${action.values && action.values.length ? `<div class="action-detail"><strong>Values:</strong> ${action.values.join(', ')}</div>` : ''}
            ${action.old_values && action.old_values.length ? `<div class="action-detail"><strong>Old Values:</strong> ${action.old_values.join(', ')}</div>` : ''}
            ${action.reason ? `<div class="action-detail"><strong>Reason:</strong> ${action.reason}</div>` : ''}
            <div class="action-buttons">
                <button class="btn btn-confirm" onclick="confirmAction()">Confirm & Create CR</button>
                <button class="btn btn-cancel" onclick="cancelAction()">Cancel</button>
            </div>
        </div>
    `;
}

async function confirmAction() {
    if (!pendingAction) return;

    const el = document.getElementById('chat-pending-action');
    el.innerHTML = '<div class="spinner"></div> Creating Change Request...';

    try {
        const result = await fetch('/api/chat/confirm', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                session_id: chatSessionId,
                action: pendingAction.action,
                record_name: pendingAction.record_name,
                zone: pendingAction.zone,
                record_type: pendingAction.record_type,
                ttl: pendingAction.ttl || 3600,
                values: pendingAction.values || [],
                old_values: pendingAction.old_values || [],
                reason: pendingAction.reason || '',
            }),
        }).then(r => r.json());

        if (result.cr_number) {
            appendChatMessage('assistant',
                `Change Request <strong>${result.cr_number}</strong> created successfully!<br><br>` +
                `Workflow: <strong>${result.workflow_id}</strong><br>` +
                `Status: <strong>${result.status}</strong><br><br>` +
                `The system is now polling for CR approval. Once approved, it will automatically:<br>` +
                `1. Run pre-checks<br>2. Implement the DNS change<br>3. Run post-checks<br>4. Close the CR<br><br>` +
                `You can track progress in the <strong>Workflows</strong> tab.`
            );
            el.innerHTML = `<div style="color: var(--accent); font-weight: 600;">CR ${result.cr_number} created</div><div style="font-size:12px; margin-top:4px;">Workflow: ${result.workflow_id}</div>`;
        } else {
            appendChatMessage('assistant', `Failed to create CR: ${result.error || 'Unknown error'}`);
            el.innerHTML = 'Action failed. Try again.';
        }
    } catch (e) {
        appendChatMessage('assistant', 'Error creating Change Request. Please try again.');
        el.innerHTML = 'Error occurred.';
    }

    pendingAction = null;
    refreshWorkflows();
    refreshRecentChanges();
}

function cancelAction() {
    pendingAction = null;
    document.getElementById('chat-pending-action').innerHTML = 'Action cancelled.';
    appendChatMessage('assistant', 'Action cancelled. What else can I help with?');
}

async function loadManagedZones() {
    try {
        const data = await API.get('/api/dns/managed-zones');
        const el = document.getElementById('chat-managed-zones');
        if (data.zones && data.zones.length) {
            el.innerHTML = data.zones.map(z => `<span class="zone-tag">${z}</span>`).join(' ');
        } else {
            el.innerHTML = 'No managed zones configured';
        }
    } catch (e) {
        document.getElementById('chat-managed-zones').innerHTML = 'Failed to load';
    }
}

async function refreshRecentChanges() {
    try {
        const changes = await API.get('/api/dns/changes?limit=10');
        const el = document.getElementById('chat-recent-changes');
        if (changes && changes.length) {
            el.innerHTML = changes.reverse().map(c => `
                <div class="change-item">
                    <span class="change-op ${c.operation}">${c.operation}</span>
                    <strong>${c.fqdn}</strong> (${c.record_type})
                    ${c.cr_number ? `<br><span style="font-size:11px; color: var(--text-secondary);">CR: ${c.cr_number} | ${c.status}</span>` : ''}
                </div>
            `).join('');
        } else {
            el.innerHTML = 'No recent changes';
        }
    } catch (e) { /* ignore */ }
}

// =============================================================================
// Workflows
// =============================================================================
let selectedWorkflowId = null;

async function refreshWorkflows() {
    try {
        const workflows = await API.get('/api/workflows');
        updateWorkflowsList(workflows);
        const active = workflows.filter(w => !['completed', 'failed', 'cancelled'].includes(w.status));
        updateActiveWorkflowsList(active);
        if (selectedWorkflowId) {
            const wf = workflows.find(w => w.workflow_id === selectedWorkflowId);
            if (wf) showWorkflowDetail(wf);
        }
    } catch (e) { console.error(e); }
}

function updateWorkflowsList(workflows) {
    const el = document.getElementById('all-workflows-list');
    if (!workflows || !workflows.length) {
        el.innerHTML = 'No workflows yet';
        return;
    }
    el.innerHTML = workflows.reverse().map(wf => renderWorkflowCard(wf)).join('');
    el.querySelectorAll('.workflow-card').forEach(card => {
        card.addEventListener('click', () => {
            el.querySelectorAll('.workflow-card').forEach(c => c.classList.remove('selected'));
            card.classList.add('selected');
            selectedWorkflowId = card.dataset.id;
            const wf = workflows.find(w => w.workflow_id === card.dataset.id);
            if (wf) showWorkflowDetail(wf);
        });
    });
}

function updateActiveWorkflowsList(workflows) {
    const el = document.getElementById('active-workflows-list');
    if (!workflows || !workflows.length) {
        el.innerHTML = '<div style="color: var(--text-secondary); font-size: 13px;">No active workflows</div>';
        return;
    }
    el.innerHTML = workflows.map(wf => renderWorkflowCard(wf)).join('');
    el.querySelectorAll('.workflow-card').forEach(card => {
        card.addEventListener('click', () => {
            selectedWorkflowId = card.dataset.id;
            const wf = workflows.find(w => w.workflow_id === card.dataset.id);
            if (wf) showWorkflowDetail(wf);
        });
    });
}

function renderWorkflowCard(wf) {
    const change = wf.change || {};
    const time = new Date(wf.created_at * 1000).toLocaleString();
    return `
        <div class="workflow-card" data-id="${wf.workflow_id}">
            <div class="workflow-header">
                <div>
                    <span class="workflow-id">${wf.workflow_id}</span>
                    ${wf.cr_number ? `<span class="workflow-cr">${wf.cr_number}</span>` : ''}
                </div>
                <span class="workflow-status-badge ${wf.status}">${wf.status.replace(/_/g, ' ')}</span>
            </div>
            <div class="workflow-desc">
                <span class="change-op ${change.operation || ''}">${(change.operation || '').toUpperCase()}</span>
                ${change.fqdn || ''} (${change.record_type || ''})
                <span style="float:right; font-size:11px; color: var(--text-secondary);">${time}</span>
            </div>
        </div>
    `;
}

function showWorkflowDetail(wf) {
    const el = document.getElementById('workflow-detail');
    const change = wf.change || {};

    let stepsHtml = '<div class="workflow-steps">';
    for (const step of (wf.steps || [])) {
        stepsHtml += `
            <div class="wf-step ${step.status}">
                <div>
                    <div class="wf-step-name">${step.name}</div>
                    <div class="wf-step-msg">${step.message || step.status}</div>
                    ${step.timestamp ? `<div class="wf-step-msg">${new Date(step.timestamp * 1000).toLocaleTimeString()}</div>` : ''}
                </div>
            </div>
        `;
    }
    stepsHtml += '</div>';

    el.innerHTML = `
        <div style="margin-bottom: 16px;">
            <strong>Workflow:</strong> ${wf.workflow_id} |
            <strong>CR:</strong> ${wf.cr_number || 'Pending'} |
            <strong>Status:</strong> <span class="workflow-status-badge ${wf.status}">${wf.status.replace(/_/g, ' ')}</span>
        </div>
        <div style="margin-bottom: 16px;">
            <strong>Operation:</strong> <span class="change-op ${change.operation}">${(change.operation || '').toUpperCase()}</span>
            <strong>${change.fqdn}</strong> (${change.record_type}) |
            <strong>Values:</strong> ${(change.values || []).join(', ') || 'N/A'} |
            <strong>TTL:</strong> ${change.ttl || 3600}s
        </div>
        <h4 style="margin-bottom: 8px;">Workflow Steps</h4>
        ${stepsHtml}
    `;
}

// =============================================================================
// Init
// =============================================================================
async function init() {
    await refreshAll();
    loadLLMMode();
    loadAzure();
    loadConfig();
    loadManagedZones();
    loadLocalDNS();
    refreshRecentChanges();
    refreshWorkflows();
    loadRemediation();
    // Auto-refresh every 30 seconds
    autoRefreshInterval = setInterval(() => {
        refreshAll();
        refreshWorkflows();
    }, 30000);
}

init();
