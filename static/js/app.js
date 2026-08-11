/**
 * LogAI Monitor - Main JavaScript
 */

// Global state
const state = {
    socket: null,
    stats: {},
    logs: [],
    filters: [],
    alerts: [],
    connected: false,
    currentPage: 'dashboard',
    seenMessages: new Map(), // Track seen messages for duplicate detection: "host::message" -> {count, elementId}
    hideDuplicates: false    // Global flag for hiding duplicates
};

// Theme Management
function applyTheme(theme) {
    const mainContent = document.querySelector('.main-content');
    if (mainContent) {
        // Remove all theme classes
        mainContent.classList.remove('theme-default', 'theme-terminal');
        // Add the selected theme class
        mainContent.classList.add(`theme-${theme || 'default'}`);
    }
    
    // Also update the html element class for instant loading styles
    if (theme === 'terminal') {
        document.documentElement.classList.add('terminal-theme-active');
    } else {
        document.documentElement.classList.remove('terminal-theme-active');
    }
    
    // Store in localStorage for immediate application on page load
    localStorage.setItem('ui_theme', theme || 'default');
}

// Available themes cycle order
const THEMES = ['default', 'terminal'];
const THEME_META = {
    default:  { icon: 'fa-moon',      label: 'Default',  title: 'Switch to Terminal theme' },
    terminal: { icon: 'fa-terminal',  label: 'Terminal', title: 'Switch to Default theme'  }
};

function toggleTheme() {
    const current = localStorage.getItem('ui_theme') || 'default';
    const idx = THEMES.indexOf(current);
    const next = THEMES[(idx + 1) % THEMES.length];
    applyTheme(next);
    updateThemeToggleBtn(next);
}

function updateThemeToggleBtn(theme) {
    const btn   = document.getElementById('themeToggleBtn');
    const icon  = document.getElementById('themeToggleIcon');
    const label = document.getElementById('themeToggleLabel');
    if (!btn) return;
    const meta = THEME_META[theme] || THEME_META['default'];
    // Swap icon
    if (icon) {
        icon.className = `fas ${meta.icon}`;
    }
    if (label) label.textContent = meta.label;
    btn.title = meta.title;
    // Style button to match terminal theme
    if (theme === 'terminal') {
        btn.style.borderColor = '#00aa00';
        btn.style.color = '#00ff00';
        btn.style.backgroundColor = '#001a00';
    } else {
        btn.style.borderColor = 'rgba(0,0,0,0.15)';
        btn.style.color = '#555';
        btn.style.backgroundColor = '';
    }
}

function previewTheme(theme) {
    const preview = document.getElementById('themePreview');
    if (preview) {
        preview.style.display = 'block';
        if (theme === 'terminal') {
            preview.style.backgroundColor = '#0a0a0a';
            preview.style.color = '#00ff00';
            preview.style.border = '1px solid #00ff00';
            preview.innerHTML = '<strong style="color: #00ff00;">$ Preview:</strong> <span style="color: #33ff33;">Terminal theme will be applied after saving.</span>';
        } else {
            preview.style.backgroundColor = '#f4f5f7';
            preview.style.color = '#333';
            preview.style.border = '1px solid #dee2e6';
            preview.innerHTML = '<strong>Preview:</strong> Default theme will be applied after saving.';
        }
    }
}

// Load theme on page load (before DOM fully loaded to prevent flash)
(function() {
    const savedTheme = localStorage.getItem('ui_theme') || 'default';
    document.documentElement.setAttribute('data-theme', savedTheme);
})();

// Initialize Socket.IO
function initSocket() {
    state.socket = io();
    
    state.socket.on('connect', () => {
        state.connected = true;
        updateConnectionStatus(true);
        console.log('Connected to server');
    });
    
    state.socket.on('disconnect', () => {
        state.connected = false;
        updateConnectionStatus(false);
        console.log('Disconnected from server');
    });
    
    state.socket.on('new_log', (log) => {
        addNewLog(log);
    });
    
    state.socket.on('new_alert', (alert) => {
        addNewAlert(alert);
        //showToast('New Alert', `${alert.filter_name}: ${alert.message.substring(0, 50)}...`, 'warning');
    });
    
    state.socket.on('analysis_complete', (data) => {
        showToast('Analysis Complete', `Analyzed ${data.logs_analyzed} logs`, 'info');
        if (data.analysis) {
            updateAnalysisDisplay(data.analysis);
        }
    });
}

// Update connection status indicator
function updateConnectionStatus(connected) {
    const indicator = document.getElementById('connectionStatus');
    if (indicator) {
        indicator.innerHTML = connected 
            ? '<span class="status-dot"></span> Connected'
            : '<span class="status-dot offline"></span> Disconnected';
    }
}

// Fetch stats
async function fetchStats() {
    try {
        const response = await fetch('/api/stats');
        state.stats = await response.json();
        updateStatsDisplay();
    } catch (error) {
        console.error('Error fetching stats:', error);
    }
}

// Update stats display
function updateStatsDisplay() {
    const elements = {
        'totalLogs': state.stats.total_logs || 0,
        'logsLastHour': state.stats.logs_last_hour || 0,
        'logsLastDay': state.stats.logs_last_day || 0,
        'totalAlerts': state.stats.unacknowledged_alerts || 0,
        'totalFilters': state.stats.total_filters || 0,
        'totalSources': state.stats.sources?.length || 0
    };
    
    for (const [id, value] of Object.entries(elements)) {
        const el = document.getElementById(id);
        if (el) el.textContent = value;
    }
    
    // Update alert badge in sidebar
    const alertBadge = document.getElementById('alertBadge');
    if (alertBadge) {
        alertBadge.textContent = state.stats.unacknowledged_alerts || 0;
        alertBadge.style.display = state.stats.unacknowledged_alerts > 0 ? 'block' : 'none';
    }
    
    // Update service status
    updateServiceStatus('redisStatus', 'Redis', state.stats.redis_connected);
    updateServiceStatus('ollamaStatus', 'Ollama AI', state.stats.ollama_available);
    updateServiceStatus('telegramStatus', 'Telegram', state.stats.telegram_enabled);
}

function updateServiceStatus(elementId, serviceName, status) {
    const el = document.getElementById(elementId);
    if (el) {
        el.innerHTML = status 
            ? `<span class="status-dot"></span> ${serviceName}: Connected`
            : `<span class="status-dot offline"></span> ${serviceName}: Offline`;
    }
}

// Fetch logs
async function fetchLogs(params = {}) {
    try {
        // Build query params, filtering out undefined/empty values
        const queryParams = new URLSearchParams();
        queryParams.set('limit', params.limit || 100);
        queryParams.set('offset', params.offset || 0);
        
        if (params.host && params.host !== '') {
            queryParams.set('host', params.host);
        }
        if (params.source && params.source !== '') {
            queryParams.set('source', params.source);
        }
        if (params.severity && params.severity !== '') {
            queryParams.set('severity', params.severity);
        }
        if (params.search && params.search !== '') {
            queryParams.set('search', params.search);
        }
        
        const response = await fetch(`/api/logs?${queryParams}`);
        const data = await response.json();
        state.logs = data.logs;
        renderLogs();
        
        // Update log count display
        const logCountEl = document.getElementById('logCount');
        if (logCountEl) {
            logCountEl.textContent = data.count || state.logs.length;
        }
    } catch (error) {
        console.error('Error fetching logs:', error);
    }
}

// Current active filters (to check against new logs)
const activeFilters = {
    host: '',
    severity: '',
    search: ''
};

// Normalize a message for duplicate comparison by removing timestamps, durations, and variable parts
function normalizeMessageForComparison(message) {
    if (!message) return '';
    
    let normalized = message;
    
    // Remove syslog-style timestamps at the start: "Feb  1 20:30:46" 
    normalized = normalized.replace(/^[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+/, '');
    
    // Remove ISO-style dates/times: "2026/02/01 - 20:30:46" or "2026-02-01 20:30:46"
    normalized = normalized.replace(/\d{4}[\/\-]\d{2}[\/\-]\d{2}\s*[-\s]*\d{2}:\d{2}:\d{2}/g, 'TIMESTAMP');
    
    // Remove time patterns: "20:30:46" or "[20:30:46]"
    normalized = normalized.replace(/\[?\d{2}:\d{2}:\d{2}\]?/g, 'TIME');
    
    // Remove durations like "1.235313ms", "500ms", "1.5s", "100µs", "2.5m"
    normalized = normalized.replace(/\d+\.?\d*\s*(ms|µs|us|ns|s|m|h)\b/gi, 'DURATION');
    
    // Remove common numeric patterns that vary (PIDs in brackets like [930], line numbers)
    normalized = normalized.replace(/\[\d+\]/g, '[PID]');
    
    // Remove standalone numbers that might be request IDs, etc. (but keep IPs)
    // Keep IP addresses by not replacing patterns like 192.168.x.x
    
    // Normalize whitespace
    normalized = normalized.replace(/\s+/g, ' ').trim();
    
    return normalized;
}

// Generate a key for duplicate detection
function getLogDuplicateKey(log) {
    const host = log.hostname || log.source || 'unknown';
    const normalizedMsg = normalizeMessageForComparison(log.message);
    return `${host}::${normalizedMsg}`;
}

// Check if hide duplicates is enabled
function isHideDuplicatesEnabled() {
    const checkbox = document.getElementById('hideDuplicates');
    return checkbox?.checked || state.hideDuplicates;
}

// Add new log (from websocket)
function addNewLog(log) {
    const duplicateKey = getLogDuplicateKey(log);
    const hideDuplicates = isHideDuplicatesEnabled();
    
    // Check if this is a duplicate
    if (hideDuplicates && state.seenMessages.has(duplicateKey)) {
        // This is a duplicate - update the count on existing element
        const existing = state.seenMessages.get(duplicateKey);
        existing.count++;
        
        // Update the badge on the existing element
        const existingElement = document.querySelector(`[data-duplicate-key="${CSS.escape(duplicateKey)}"]`);
        if (existingElement) {
            let badge = existingElement.querySelector('.duplicate-badge');
            if (!badge) {
                // Create badge if it doesn't exist
                const metaDiv = existingElement.querySelector('.log-meta');
                if (metaDiv) {
                    badge = document.createElement('span');
                    badge.className = 'duplicate-badge';
                    metaDiv.appendChild(badge);
                }
            }
            if (badge) {
                badge.textContent = `+${existing.count}`;
                badge.title = `${existing.count} duplicate message(s) hidden`;
                // Flash the badge to show update
                badge.classList.add('flash');
                setTimeout(() => badge.classList.remove('flash'), 300);
            }
        }
        
        // Still add to state but don't render
        state.logs.unshift(log);
        if (state.logs.length > 500) {
            state.logs.pop();
        }
        
        // Update stats (throttled)
        throttledFetchStats();
        return; // Don't add to DOM
    }
    
    // Not a duplicate (or duplicates not hidden) - add normally
    state.logs.unshift(log);
    if (state.logs.length > 500) {
        state.logs.pop();
    }
    
    // Track this message for future duplicate detection
    if (hideDuplicates) {
        state.seenMessages.set(duplicateKey, { count: 0, logId: log.id });
    }
    
    // If we are on the AI Analysis page, re-render so we keep the visible list capped
    if (state.currentPage === 'analysis') {
        renderLogs();
        // Update stats (throttled)
        throttledFetchStats();
        return;
    }
    
    // Check if we're on the logs page and if log matches current filters
    const logsContainer = document.getElementById('logsContainer');
    if (logsContainer) {
        // Check if log matches active filters
        if (activeFilters.host && (log.hostname || log.source) !== activeFilters.host) {
            return; // Don't display - doesn't match host filter
        }
        if (activeFilters.severity && log.severity !== activeFilters.severity) {
            return; // Don't display - doesn't match severity filter
        }
        if (activeFilters.search) {
            const searchLower = activeFilters.search.toLowerCase();
            const message = (log.message || '').toLowerCase();
            const source = (log.source || '').toLowerCase();
            const hostname = (log.hostname || '').toLowerCase();
            const program = (log.program || '').toLowerCase();
            if (!message.includes(searchLower) && !source.includes(searchLower) && !hostname.includes(searchLower) && !program.includes(searchLower)) {
                return; // Don't display - doesn't match search filter
            }
        }

        const logElement = createLogElement(log, hideDuplicates);
        if (hideDuplicates) {
            logElement.dataset.duplicateKey = duplicateKey;
        }
        logElement.classList.add('new');
        logsContainer.insertBefore(logElement, logsContainer.firstChild);
        
        // Smoothly fade out the new indicator after a delay
        setTimeout(() => {
            logElement.classList.add('fade-out');
            setTimeout(() => {
                logElement.classList.remove('new', 'fade-out');
            }, 3000);
        }, 500);
        
        // Remove old logs from DOM
        while (logsContainer.children.length > 500) {
            logsContainer.removeChild(logsContainer.lastChild);
        }
        
        // Update count
        const logCountEl = document.getElementById('logCount');
        if (logCountEl) {
            const currentCount = parseInt(logCountEl.textContent) || 0;
            logCountEl.textContent = currentCount + 1;
        }
    }
    
    // Update stats (throttled)
    throttledFetchStats();
}

// Throttle stats fetching to prevent overload
let statsTimeout = null;
function throttledFetchStats() {
    if (statsTimeout) return;
    statsTimeout = setTimeout(() => {
        fetchStats();
        statsTimeout = null;
    }, 1000);
}

// Render logs
function renderLogs() {
    const container = document.getElementById('logsContainer');
    if (!container) return;
    
    // Update global state from checkbox
    const hideDuplicatesCheckbox = document.getElementById('hideDuplicates');
    const hideDuplicates = hideDuplicatesCheckbox?.checked || false;
    state.hideDuplicates = hideDuplicates;
    
    // Reset seen messages map when rendering
    state.seenMessages.clear();
    
    if (state.logs.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <i class="fas fa-file-alt"></i>
                <h4>No logs found</h4>
                <p>Logs will appear here when received from rsyslog or Docker</p>
            </div>
        `;
        updateLogCount(0);
        return;
    }
    
    let logsToRender = state.logs;
    let duplicateCount = 0;
    
    if (hideDuplicates) {
        // Track seen messages per host to hide duplicates
        const filteredLogs = [];
        
        for (const log of state.logs) {
            const key = getLogDuplicateKey(log);
            
            if (!state.seenMessages.has(key)) {
                state.seenMessages.set(key, { count: 0, logId: log.id });
                // Clone the log and add duplicate count later
                filteredLogs.push({ ...log, _duplicateCount: 0, _duplicateKey: key });
            } else {
                // Increment count for this duplicate
                state.seenMessages.get(key).count++;
                duplicateCount++;
            }
        }
        
        // Update duplicate counts on the filtered logs
        filteredLogs.forEach(log => {
            const info = state.seenMessages.get(log._duplicateKey);
            if (info && info.count > 0) {
                log._duplicateCount = info.count;
            }
        });
        
        logsToRender = filteredLogs;
    }
    
    container.innerHTML = '';

    // Keep a copy of the full list for the info badge
    const fullCount = logsToRender.length;

    // If on analysis page, show more logs (scrollable)
    let displayedLogs = logsToRender;
    if (state.currentPage === 'analysis') {
        const MAX_ANALYSIS_LOGS = 25;
        displayedLogs = logsToRender.slice(0, MAX_ANALYSIS_LOGS);
    }

    displayedLogs.forEach(log => {
        const element = createLogElement(log, hideDuplicates);
        if (hideDuplicates && log._duplicateKey) {
            element.dataset.duplicateKey = log._duplicateKey;
        }
        container.appendChild(element);
    });
    
    // Update log count
    updateLogCount(displayedLogs.length, duplicateCount);

    // Update Recent Logs info badge when present
    const infoEl = document.getElementById('recentLogsInfo');
    if (infoEl) {
        infoEl.textContent = `Showing ${displayedLogs.length}${fullCount > displayedLogs.length ? ' of ' + fullCount : ''}`;
    }
}

// Update log count display
function updateLogCount(count, hiddenDuplicates = 0) {
    const countEl = document.getElementById('logCount');
    if (countEl) {
        if (hiddenDuplicates > 0) {
            countEl.innerHTML = `${count} <span style="color: #666; font-size: 0.85rem;">(${hiddenDuplicates} duplicates hidden)</span>`;
        } else {
            countEl.textContent = count;
        }
    }
}

// Create log element
function createLogElement(log, showDuplicateBadge = false) {
    const div = document.createElement('div');
    div.className = 'log-entry';
    div.dataset.id = log.id;
    
    const timestamp = new Date(log.timestamp).toLocaleString();
    const duplicateBadge = (showDuplicateBadge && log._duplicateCount > 0) 
        ? `<span class="duplicate-badge" title="${log._duplicateCount} duplicate message(s) hidden">+${log._duplicateCount}</span>` 
        : '';
    
    const hostname = log.hostname || log.source || 'unknown';
    
    div.innerHTML = `
        <div class="log-meta">
            <span class="severity-badge ${log.severity}">${log.severity}</span>
            <span>${timestamp}</span>
            <span class="log-host" title="${escapeHtml(hostname)}">${escapeHtml(hostname)}</span>
            <span>${log.program || '-'}</span>
            ${duplicateBadge}
        </div>
        <div class="log-message">${escapeHtml(log.message)}</div>
    `;
    
    div.onclick = () => showLogDetail(log);
    
    return div;
}

// Show log detail modal
function showLogDetail(log) {
    const modal = document.getElementById('logDetailModal');
    if (!modal) return;
    
    document.getElementById('logDetailContent').innerHTML = `
        <div class="form-group">
            <label class="form-label">ID</label>
            <code>${log.id}</code>
        </div>
        <div class="form-group">
            <label class="form-label">Timestamp</label>
            <p>${new Date(log.timestamp).toLocaleString()}</p>
        </div>
        <div class="form-group">
            <label class="form-label">Source</label>
            <p>${log.source} (${log.source_type})</p>
        </div>
        <div class="form-group">
            <label class="form-label">Severity</label>
            <span class="severity-badge ${log.severity}">${log.severity}</span>
        </div>
        <div class="form-group">
            <label class="form-label">Hostname</label>
            <p>${log.hostname || '-'}</p>
        </div>
        <div class="form-group">
            <label class="form-label">Program</label>
            <p>${log.program || '-'}</p>
        </div>
        <div class="form-group">
            <label class="form-label">Message</label>
            <pre>${escapeHtml(log.message)}</pre>
        </div>
        ${log.analysis ? `
        <div class="form-group">
            <label class="form-label">AI Analysis</label>
            <div class="analysis-result">${formatAnalysisResult(log.analysis)}</div>
        </div>
        ` : ''}
    `;
    
    // Store current log for analysis
    modal.dataset.logId = log.id;
    
    openModal('logDetailModal');
}

// Fetch filters
async function fetchFilters() {
    try {
        const response = await fetch('/api/filters');
        state.filters = await response.json();
        renderFilters();
    } catch (error) {
        console.error('Error fetching filters:', error);
    }
}

// Render filters
function renderFilters() {
    const container = document.getElementById('filtersContainer');
    if (!container) return;
    
    if (state.filters.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <i class="fas fa-filter"></i>
                <h4>No filters created</h4>
                <p>Create filters to monitor specific log patterns and receive alerts</p>
            </div>
        `;
        return;
    }
    
    container.innerHTML = `
        <table>
            <thead>
                <tr>
                    <th>Name</th>
                    <th>Conditions</th>
                    <th>Notify</th>
                    <th>Status</th>
                    <th>Actions</th>
                </tr>
            </thead>
            <tbody>
                ${state.filters.map(filter => `
                    <tr>
                        <td><strong>${escapeHtml(filter.name)}</strong></td>
                        <td>
                            ${filter.conditions?.severity ? `<span class="severity-badge ${filter.conditions.severity}">${filter.conditions.severity}</span>` : ''}
                            ${filter.conditions?.source_contains ? `Source: ${escapeHtml(filter.conditions.source_contains)}` : ''}
                            ${filter.conditions?.message_contains ? `Message: ${escapeHtml(filter.conditions.message_contains)}` : ''}
                        </td>
                        <td>${filter.notify_telegram ? '📱 Telegram' : '-'}</td>
                        <td>
                            <label class="toggle-switch">
                                <input type="checkbox" ${filter.enabled ? 'checked' : ''} 
                                    onchange="toggleFilter('${filter.id}', this.checked)">
                                <span class="toggle-slider"></span>
                            </label>
                        </td>
                        <td>
                            <button class="btn btn-sm btn-outline" onclick="editFilter('${filter.id}')">
                                <i class="fas fa-edit"></i>
                            </button>
                            <button class="btn btn-sm btn-danger" onclick="deleteFilter('${filter.id}')">
                                <i class="fas fa-trash"></i>
                            </button>
                        </td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

// Create filter
async function createFilter(formData) {
    try {
        const response = await fetch('/api/filters', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(formData)
        });
        
        if (response.ok) {
            showToast('Success', 'Filter created successfully', 'success');
            closeModal('filterModal');
            fetchFilters();
            fetchStats();
        } else {
            showToast('Error', 'Failed to create filter', 'error');
        }
    } catch (error) {
        console.error('Error creating filter:', error);
        showToast('Error', 'Failed to create filter', 'error');
    }
}

// Update filter
async function updateFilter(filterId, formData) {
    try {
        const response = await fetch(`/api/filters/${filterId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(formData)
        });
        
        if (response.ok) {
            showToast('Success', 'Filter updated successfully', 'success');
            closeModal('filterModal');
            fetchFilters();
        } else {
            showToast('Error', 'Failed to update filter', 'error');
        }
    } catch (error) {
        console.error('Error updating filter:', error);
        showToast('Error', 'Failed to update filter', 'error');
    }
}

// Toggle filter enabled/disabled
async function toggleFilter(filterId, enabled) {
    try {
        const filter = state.filters.find(f => f.id === filterId);
        if (filter) {
            filter.enabled = enabled;
            await updateFilter(filterId, filter);
        }
    } catch (error) {
        console.error('Error toggling filter:', error);
    }
}

// Delete filter
async function deleteFilter(filterId) {
    if (!confirm('Are you sure you want to delete this filter?')) return;
    
    try {
        const response = await fetch(`/api/filters/${filterId}`, {
            method: 'DELETE'
        });
        
        if (response.ok) {
            showToast('Success', 'Filter deleted', 'success');
            fetchFilters();
            fetchStats();
        } else {
            showToast('Error', 'Failed to delete filter', 'error');
        }
    } catch (error) {
        console.error('Error deleting filter:', error);
        showToast('Error', 'Failed to delete filter', 'error');
    }
}

// Edit filter
function editFilter(filterId) {
    const filter = state.filters.find(f => f.id === filterId);
    if (!filter) return;
    
    document.getElementById('filterModalTitle').textContent = 'Edit Filter';
    document.getElementById('filterId').value = filter.id;
    document.getElementById('filterName').value = filter.name;
    document.getElementById('filterSeverity').value = filter.conditions?.severity || '';
    document.getElementById('filterSourceContains').value = filter.conditions?.source_contains || '';
    document.getElementById('filterMessageContains').value = filter.conditions?.message_contains || '';
    document.getElementById('filterMessageRegex').value = filter.conditions?.message_regex || '';
    document.getElementById('filterNotifyTelegram').checked = filter.notify_telegram || false;
    document.getElementById('filterEnabled').checked = filter.enabled !== false;
    
    openModal('filterModal');
}

// Show new filter modal
function showNewFilterModal() {
    document.getElementById('filterModalTitle').textContent = 'Create Filter';
    document.getElementById('filterForm').reset();
    document.getElementById('filterId').value = '';
    // Explicitly set checkbox states for new filter
    document.getElementById('filterNotifyTelegram').checked = false;
    document.getElementById('filterEnabled').checked = true;
    openModal('filterModal');
}

// Fetch alerts
async function fetchAlerts() {
    try {
        const response = await fetch('/api/alerts?limit=100');
        state.alerts = await response.json();
        renderAlerts();
    } catch (error) {
        console.error('Error fetching alerts:', error);
    }
}

// Add new alert
function addNewAlert(alert) {
    state.alerts.unshift(alert);
    renderAlerts();
}

// Render alerts
function renderAlerts() {
    const container = document.getElementById('alertsContainer');
    if (!container) return;
    
    if (state.alerts.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <i class="fas fa-bell"></i>
                <h4>No alerts</h4>
                <p>Alerts will appear here when filters match incoming logs</p>
            </div>
        `;
        return;
    }
    
    container.innerHTML = `
        <table>
            <thead>
                <tr>
                    <th>Time</th>
                    <th>Severity</th>
                    <th>Filter</th>
                    <th>Host</th>
                    <th>Source</th>
                    <th>Message</th>
                    <th>Status</th>
                    <th>Actions</th>
                </tr>
            </thead>
            <tbody>
                ${state.alerts.map(alert => `
                    <tr class="${alert.acknowledged ? '' : 'unread'}">
                        <td>${new Date(alert.timestamp).toLocaleString()}</td>
                        <td><span class="severity-badge ${alert.severity}">${alert.severity}</span></td>
                        <td>${escapeHtml(alert.filter_name || '-')}</td>
                        <td><span class="log-host" title="${escapeHtml(alert.hostname || alert.source || '-')}">${escapeHtml(alert.hostname || alert.source || '-')}</span></td>
                        <td>${escapeHtml(alert.source || '-')}</td>
                        <td>${escapeHtml(alert.message?.substring(0, 100) || '-')}...</td>
                        <td>${alert.acknowledged ? '✓ Acknowledged' : '⚠ New'}</td>
                        <td>
                            ${!alert.acknowledged ? `
                                <button class="btn btn-sm btn-success" onclick="acknowledgeAlert('${alert.id}')">
                                    <i class="fas fa-check"></i> Ack
                                </button>
                            ` : ''}
                        </td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

// Acknowledge alert
async function acknowledgeAlert(alertId) {
    try {
        const response = await fetch(`/api/alerts/${alertId}/acknowledge`, {
            method: 'POST'
        });
        
        if (response.ok) {
            const alert = state.alerts.find(a => a.id === alertId);
            if (alert) alert.acknowledged = true;
            renderAlerts();
            fetchStats();
        }
    } catch (error) {
        console.error('Error acknowledging alert:', error);
    }
}

// Acknowledge all alerts
async function acknowledgeAllAlerts() {
    try {
        const response = await fetch('/api/alerts/acknowledge-all', {
            method: 'POST'
        });
        
        if (response.ok) {
            const data = await response.json();
            showToast('Success', `Acknowledged ${data.acknowledged} alerts`, 'success');
            // Mark all alerts as acknowledged in state
            state.alerts.forEach(a => a.acknowledged = true);
            renderAlerts();
            fetchStats();
        }
    } catch (error) {
        console.error('Error acknowledging all alerts:', error);
        showToast('Error', 'Failed to acknowledge alerts', 'error');
    }
}

// Clear acknowledged alerts
async function clearAcknowledgedAlerts() {
    if (!confirm('Are you sure you want to delete all acknowledged alerts? This cannot be undone.')) {
        return;
    }
    
    try {
        const response = await fetch('/api/alerts/clear', {
            method: 'POST'
        });
        
        if (response.ok) {
            const data = await response.json();
            showToast('Success', `Deleted ${data.deleted} alerts`, 'success');
            // Remove acknowledged alerts from state
            state.alerts = state.alerts.filter(a => !a.acknowledged);
            renderAlerts();
            fetchStats();
        }
    } catch (error) {
        console.error('Error clearing alerts:', error);
        showToast('Error', 'Failed to clear alerts', 'error');
    }
}

// Fetch Docker containers
async function fetchContainers() {
    try {
        // Load settings to get current exclusion list
        const settingsResponse = await fetch('/api/settings');
        const settings = await settingsResponse.json();
        const excludedContainers = settings.docker_excluded_containers || [];
        const dockerEnabled = settings.docker_enabled !== false;
        
        // Update the global enable toggle
        const enableToggle = document.getElementById('dockerEnabledToggle');
        if (enableToggle) {
            enableToggle.checked = dockerEnabled;
        }
        
        const response = await fetch('/api/docker/containers');
        const containers = await response.json();
        renderContainers(containers, excludedContainers);
    } catch (error) {
        console.error('Error fetching containers:', error);
    }
}

// Toggle Docker collection globally
async function toggleDockerCollection(enabled) {
    try {
        const settingsResponse = await fetch('/api/settings');
        const settings = await settingsResponse.json();
        settings.docker_enabled = enabled;
        
        await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings)
        });
        
        showToast('Success', `Docker log collection ${enabled ? 'enabled' : 'disabled'}`, 'success');
    } catch (error) {
        console.error('Error toggling Docker collection:', error);
        showToast('Error', 'Failed to update setting', 'error');
    }
}

// Toggle exclude all containers
async function toggleExcludeAll(excludeAll) {
    const checkboxes = document.querySelectorAll('.container-exclude-checkbox');
    checkboxes.forEach(cb => cb.checked = excludeAll);
    await saveExcludedContainers();
}

// Toggle single container exclusion
async function toggleContainerExclusion(containerName, excluded) {
    await saveExcludedContainers();
    updateExcludeAllCheckbox();
}

// Update the "Exclude All" checkbox state based on individual checkboxes
function updateExcludeAllCheckbox() {
    const checkboxes = document.querySelectorAll('.container-exclude-checkbox');
    const excludeAllCheckbox = document.getElementById('excludeAllCheckbox');
    if (!excludeAllCheckbox || checkboxes.length === 0) return;
    
    const allChecked = Array.from(checkboxes).every(cb => cb.checked);
    const someChecked = Array.from(checkboxes).some(cb => cb.checked);
    
    excludeAllCheckbox.checked = allChecked;
    excludeAllCheckbox.indeterminate = someChecked && !allChecked;
}

// Save excluded containers to settings
async function saveExcludedContainers() {
    const checkboxes = document.querySelectorAll('.container-exclude-checkbox:checked');
    const excludedContainers = Array.from(checkboxes).map(cb => cb.dataset.containerName);
    
    try {
        const settingsResponse = await fetch('/api/settings');
        const settings = await settingsResponse.json();
        settings.docker_excluded_containers = excludedContainers;
        
        await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings)
        });
        
        showToast('Success', 'Container exclusions updated', 'success');
    } catch (error) {
        console.error('Error saving excluded containers:', error);
        showToast('Error', 'Failed to save exclusions', 'error');
    }
}

// Render Docker containers
function renderContainers(containers, excludedContainers = []) {
    const container = document.getElementById('containersContainer');
    if (!container) return;
    
    if (containers.length === 0) {
        container.innerHTML = `
            <div class="empty-state">
                <i class="fab fa-docker"></i>
                <h4>No Docker containers</h4>
                <p>Docker containers will appear here when detected</p>
            </div>
        `;
        return;
    }
    
    container.innerHTML = `
        <table>
            <thead>
                <tr>
                    <th>Name</th>
                    <th>Image</th>
                    <th>Status</th>
                    <th>ID</th>
                    <th>Exclude from Logging</th>
                    <th>Actions</th>
                </tr>
            </thead>
            <tbody>
                ${containers.map(c => {
                    const isExcluded = excludedContainers.includes(c.name);
                    return `
                    <tr>
                        <td><strong>${escapeHtml(c.name)}</strong></td>
                        <td><code>${escapeHtml(c.image)}</code></td>
                        <td><span class="container-status ${c.status}">${c.status}</span></td>
                        <td><code>${c.id}</code></td>
                        <td style="text-align: center;">
                            <input type="checkbox" class="form-check-input container-exclude-checkbox" 
                                   data-container-name="${escapeHtml(c.name)}" 
                                   ${isExcluded ? 'checked' : ''}
                                   onchange="toggleContainerExclusion('${escapeHtml(c.name)}', this.checked)"
                                   title="${isExcluded ? 'Currently excluded - click to include' : 'Click to exclude from logging'}">
                        </td>
                        <td>
                            <button class="btn btn-sm btn-outline" onclick="showContainerLogs('${c.id}', '${escapeHtml(c.name)}')">
                                <i class="fas fa-file-alt"></i> Logs
                            </button>
                        </td>
                    </tr>
                `}).join('')}
            </tbody>
        </table>
    `;
    
    // Update the exclude all checkbox state
    updateExcludeAllCheckbox();
}

// Show container logs
async function showContainerLogs(containerId, containerName) {
    try {
        const response = await fetch(`/api/docker/containers/${containerId}/logs?lines=100`);
        const logs = await response.json();
        
        document.getElementById('containerLogsTitle').textContent = `Logs: ${containerName}`;
        document.getElementById('containerLogsContent').innerHTML = `<pre>${logs.join('\n')}</pre>`;
        
        openModal('containerLogsModal');
    } catch (error) {
        console.error('Error fetching container logs:', error);
        showToast('Error', 'Failed to fetch container logs', 'error');
    }
}

// AI Chat
async function sendChatMessage() {
    const input = document.getElementById('chatInput');
    const message = input.value.trim();
    if (!message) return;
    
    // Add user message
    addChatMessage(message, 'user');
    input.value = '';
    
    // Show typing indicator
    const typingId = addChatMessage('⏳ Analyzing...', 'assistant');
    
    try {
        // Build a summary context from recent logs (grouped by severity and host)
        const severityCounts = {};
        const hostCounts = {};
        const errorMessages = [];
        const warningMessages = [];
        
        state.logs.slice(0, 50).forEach(l => {
            severityCounts[l.severity] = (severityCounts[l.severity] || 0) + 1;
            const host = l.hostname || l.source || 'unknown';
            hostCounts[host] = (hostCounts[host] || 0) + 1;
            if (l.severity === 'error' || l.severity === 'critical') {
                errorMessages.push(`[${host}] ${l.program || l.source}: ${l.message.substring(0, 80)}`);
            } else if (l.severity === 'warning') {
                warningMessages.push(`[${host}] ${l.program || l.source}: ${l.message.substring(0, 80)}`);
            }
        });
        
        // Create summarized context with host info
        let context = `Log summary (last 50): `;
        context += Object.entries(severityCounts).map(([k, v]) => `${v} ${k}`).join(', ');
        context += `\nHosts: ${Object.keys(hostCounts).join(', ')}`;
        
        if (errorMessages.length > 0) {
            context += `\nRecent errors: ${errorMessages.slice(0, 5).join(' | ')}`;
        }
        if (warningMessages.length > 0) {
            context += `\nRecent warnings: ${warningMessages.slice(0, 3).join(' | ')}`;
        }
        
        // Include alerts summary
        if (state.alerts && state.alerts.length > 0) {
            const unackAlerts = state.alerts.filter(a => !a.acknowledged);
            const ackAlerts = state.alerts.filter(a => a.acknowledged);
            context += `\n\nALERTS SUMMARY:`;
            context += `\nTotal alerts: ${state.alerts.length} (${unackAlerts.length} unacknowledged, ${ackAlerts.length} acknowledged)`;
            
            if (unackAlerts.length > 0) {
                context += `\nUnacknowledged alerts:`;
                unackAlerts.slice(0, 5).forEach(a => {
                    const time = new Date(a.timestamp).toLocaleString();
                    context += `\n- [${time}] ${a.filter_name}: ${a.message.substring(0, 100)}`;
                });
            }
        } else {
            context += `\n\nALERTS: No alerts currently in the system.`;
        }
        
        const response = await fetch('/api/ollama/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: message,
                context: context
            })
        });
        
        const data = await response.json();
        
        // Update typing message with response - remove the analyzing message entirely and replace
        if (typingId) {
            const aiResponse = data.response || data.error || 'No response received';
            updateChatMessage(typingId, aiResponse);
        }
        
    } catch (error) {
        console.error('Error sending chat message:', error);
        if (typingId) {
            updateChatMessage(typingId, '❌ Error: Failed to get response from AI');
        }
    }
}

function addChatMessage(content, role) {
    const container = document.getElementById('chatMessages');
    if (!container) return null;
    
    const id = 'msg-' + Date.now() + '-' + Math.random().toString(36).substr(2, 9);
    const div = document.createElement('div');
    div.id = id;
    div.className = `chat-message ${role}`;
    div.innerHTML = `<div class="message-content">${formatChatMessage(content)}</div>`;
    
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
    
    return id;
}

function updateChatMessage(id, content) {
    if (!id) return;
    const msg = document.getElementById(id);
    if (msg) {
        const messageContent = msg.querySelector('.message-content');
        if (messageContent) {
            messageContent.innerHTML = formatChatMessage(content);
        }
        // Scroll to bottom after updating message
        const container = document.getElementById('chatMessages');
        if (container) {
            container.scrollTop = container.scrollHeight;
        }
    }
}

// Format chat message with paragraphs, bullets, and basic markdown
function formatChatMessage(text) {
    if (!text) return '';
    
    // Escape HTML first
    let formatted = escapeHtml(text);
    
    // Convert markdown-style bold **text** to <strong>
    formatted = formatted.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    
    // Convert markdown-style italic *text* to <em> (but not if already part of **)
    formatted = formatted.replace(/(?<!\*)\*([^*]+)\*(?!\*)/g, '<em>$1</em>');
    
    // Convert bullet points (lines starting with - or *)
    formatted = formatted.replace(/^[\-\*]\s+(.+)$/gm, '<li>$1</li>');
    
    // Wrap consecutive <li> items in <ul>
    formatted = formatted.replace(/(<li>.*<\/li>\n?)+/g, '<ul>$&</ul>');
    
    // Convert numbered lists (1. 2. 3. etc)
    formatted = formatted.replace(/^(\d+)\.\s+(.+)$/gm, '<li>$2</li>');
    
    // Split into paragraphs on double newlines
    const paragraphs = formatted.split(/\n\n+/);
    
    // Process each paragraph
    formatted = paragraphs.map(p => {
        // If it's already wrapped in a list, don't wrap in <p>
        if (p.trim().startsWith('<ul>') || p.trim().startsWith('<ol>') || p.trim().startsWith('<li>')) {
            return p;
        }
        // Convert single newlines to <br> within paragraphs
        p = p.replace(/\n/g, '<br>');
        // Wrap in paragraph tag if not empty
        return p.trim() ? `<p>${p}</p>` : '';
    }).join('');
    
    // Clean up any empty paragraphs
    formatted = formatted.replace(/<p><\/p>/g, '');
    
    return formatted;
}

// Format AI analysis result (handles both JSON objects and string responses)
function formatAnalysisResult(analysis) {
    if (!analysis) return '';
    
    // If it's a string (already formatted or raw text)
    if (typeof analysis === 'string') {
        // Try to parse as JSON first
        try {
            const parsed = JSON.parse(analysis);
            return formatAnalysisObject(parsed);
        } catch (e) {
            // Not JSON, format as text
            return formatChatMessage(analysis);
        }
    }
    
    // If it's an object, format it nicely
    if (typeof analysis === 'object') {
        return formatAnalysisObject(analysis);
    }
    
    return escapeHtml(String(analysis));
}

// Format an analysis object into readable HTML
function formatAnalysisObject(obj) {
    if (!obj) return '';
    
    let html = '<div class="analysis-formatted">';
    
    // Handle summary first if present
    if (obj.summary) {
        html += `<p><strong>Summary:</strong> ${escapeHtml(obj.summary)}</p>`;
    }
    
    // Handle category
    if (obj.category) {
        html += `<p><strong>Category:</strong> ${escapeHtml(obj.category)}</p>`;
    }
    
    // Handle is_critical
    if (obj.is_critical !== undefined) {
        const criticalText = obj.is_critical ? '⚠️ Yes - Requires attention' : '✅ No';
        html += `<p><strong>Critical:</strong> ${criticalText}</p>`;
    }
    
    // Handle alert_user
    if (obj.alert_user !== undefined) {
        const alertText = obj.alert_user ? '🔔 Yes' : 'No';
        html += `<p><strong>Alert Required:</strong> ${alertText}</p>`;
    }
    
    // Handle recommendation
    if (obj.recommendation) {
        html += `<p><strong>Recommendation:</strong> ${escapeHtml(obj.recommendation)}</p>`;
    }
    
    // Handle any other fields
    const handledFields = ['summary', 'category', 'is_critical', 'alert_user', 'recommendation'];
    Object.keys(obj).forEach(key => {
        if (!handledFields.includes(key) && obj[key]) {
            const label = key.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
            html += `<p><strong>${escapeHtml(label)}:</strong> ${escapeHtml(String(obj[key]))}</p>`;
        }
    });
    
    html += '</div>';
    return html;
}

// Analyze selected log with AI
async function analyzeLog() {
    const modal = document.getElementById('logDetailModal');
    const logId = modal?.dataset.logId;
    if (!logId) return;
    
    const btn = document.getElementById('analyzeLogBtn');
    btn.innerHTML = '<span class="spinner"></span> Analyzing...';
    btn.disabled = true;
    
    try {
        const response = await fetch('/api/ollama/analyze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ log_id: logId })
        });
        
        const data = await response.json();
        
        if (data.success) {
            showToast('Success', 'Analysis complete', 'success');
            // Refresh log detail
            const log = state.logs.find(l => l.id === logId);
            if (log) {
                log.analysis = data.analysis;
                showLogDetail(log);
            }
        } else {
            showToast('Error', data.error || 'Analysis failed', 'error');
        }
        
    } catch (error) {
        console.error('Error analyzing log:', error);
        showToast('Error', 'Failed to analyze log', 'error');
    } finally {
        btn.innerHTML = '<i class="fas fa-brain"></i> Analyze with AI';
        btn.disabled = false;
    }
}

// Batch analyze logs
async function analyzeAllLogs() {
    const btn = document.getElementById('analyzeAllBtn');
    btn.innerHTML = '<span class="spinner"></span> Analyzing...';
    btn.disabled = true;
    
    try {
        const response = await fetch('/api/ollama/analyze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ logs: state.logs.slice(0, 20) })
        });
        
        const data = await response.json();
        
        if (data.success) {
            showToast('Success', `Analyzed ${data.logs_analyzed} logs`, 'success');
            updateAnalysisDisplay(data.analysis);
        } else {
            showToast('Error', data.error || 'Analysis failed', 'error');
        }
        
    } catch (error) {
        console.error('Error analyzing logs:', error);
        showToast('Error', 'Failed to analyze logs', 'error');
    } finally {
        btn.innerHTML = '<i class="fas fa-brain"></i> Analyze Recent Logs';
        btn.disabled = false;
    }
}

// Update analysis display
function updateAnalysisDisplay(analysis) {
    const container = document.getElementById('analysisResult');
    if (!container) return;
    
    const statusClass = {
        'healthy': 'success',
        'warning': 'warning',
        'critical': 'danger'
    }[analysis.overall_status] || '';
    
    container.innerHTML = `
        <div class="analysis-status ${analysis.overall_status}">
            ${analysis.overall_status === 'healthy' ? '✅' : analysis.overall_status === 'warning' ? '⚠️' : '🚨'}
            ${(analysis.overall_status || 'Unknown').toUpperCase()}
        </div>
        
        ${analysis.affected_hosts?.length > 0 ? `
        <div style="margin-top: 1rem;">
            <h4><i class="fas fa-server"></i> Affected Hosts</h4>
            <div style="display: flex; flex-wrap: wrap; gap: 0.5rem;">
                ${analysis.affected_hosts.map(host => `<span class="badge" style="background: #2196F3; color: white; padding: 0.3rem 0.6rem; border-radius: 4px;">${escapeHtml(host)}</span>`).join('')}
            </div>
        </div>
        ` : ''}
        
        <div style="margin-top: 1rem;">
            <h4><i class="fas fa-exclamation-triangle"></i> Issues Found (${analysis.critical_count || 0} critical)</h4>
            ${analysis.issues_found?.length > 0 ? `
                <ul>
                    ${analysis.issues_found.map(issue => `<li>${escapeHtml(issue)}</li>`).join('')}
                </ul>
            ` : '<p>No issues found</p>'}
        </div>
        
        <div style="margin-top: 1rem;">
            <h4><i class="fas fa-lightbulb"></i> Recommendations</h4>
            ${analysis.recommendations?.length > 0 ? `
                <ul>
                    ${analysis.recommendations.map(rec => `<li>${escapeHtml(rec)}</li>`).join('')}
                </ul>
            ` : '<p>No recommendations</p>'}
        </div>
    `;
}

// Settings
let _loadSettingsInProgress = false;
async function loadSettings() {
    if (_loadSettingsInProgress) return;
    _loadSettingsInProgress = true;

    try {
        const response = await fetch('/api/settings');
        const settings = await response.json();
        
        // Populate form
        document.getElementById('telegramEnabled').checked = settings.telegram_enabled;
        document.getElementById('telegramBotToken').value = settings.telegram_bot_token || '';
        document.getElementById('telegramChatId').value = settings.telegram_chat_id || '';
        document.getElementById('telegramCooldown').value = settings.telegram_cooldown_minutes ?? 60;
        document.getElementById('ollamaEnabled').checked = settings.ollama_enabled !== false;
        document.getElementById('ollamaHost').value = settings.ollama_host || 'http://localhost:11434';
        document.getElementById('ollamaModel').value = settings.ollama_model || 'llama3.2';
        document.getElementById('analysisInterval').value = settings.analysis_interval || 300;
        document.getElementById('logRetention').value = settings.log_retention_hours || 12;
        document.getElementById('autoAnalyze').checked = settings.auto_analyze !== false;
        
        // Hide duplicates default setting
        const hideDuplicatesDefaultEl = document.getElementById('hideDuplicatesDefault');
        if (hideDuplicatesDefaultEl) {
            hideDuplicatesDefaultEl.checked = settings.hide_duplicates_default === true;
        }
        
        // UI Theme setting
        const uiThemeEl = document.getElementById('uiTheme');
        if (uiThemeEl) {
            uiThemeEl.value = settings.ui_theme || 'default';
        }
        
        // Load Ollama models
        await loadOllamaModels();
        
        // Load syslog diagnostics
        loadSyslogDiagnostics();
        
    } catch (error) {
        console.error('Error loading settings:', error);
    } finally {
        _loadSettingsInProgress = false;
    }
}

async function saveSettings() {
    // Get current settings first to preserve Docker settings
    let currentSettings = {};
    try {
        const currentResponse = await fetch('/api/settings');
        currentSettings = await currentResponse.json();
    } catch (e) {
        // Ignore, will use defaults
    }
    
    const settings = {
        ...currentSettings,  // Preserve existing settings (including Docker)
        telegram_enabled: document.getElementById('telegramEnabled').checked,
        telegram_bot_token: document.getElementById('telegramBotToken').value,
        telegram_chat_id: document.getElementById('telegramChatId').value,
        telegram_cooldown_minutes: parseInt(document.getElementById('telegramCooldown').value) || 0,
        ollama_enabled: document.getElementById('ollamaEnabled').checked,
        ollama_host: document.getElementById('ollamaHost').value,
        ollama_model: document.getElementById('ollamaModel').value,
        analysis_interval: parseInt(document.getElementById('analysisInterval').value),
        log_retention_hours: parseInt(document.getElementById('logRetention').value),
        auto_analyze: document.getElementById('autoAnalyze').checked,
        hide_duplicates_default: document.getElementById('hideDuplicatesDefault')?.checked || false,
        ui_theme: document.getElementById('uiTheme')?.value || 'default'
    };
    
    try {
        const response = await fetch('/api/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings)
        });
        
        if (response.ok) {
            showToast('Success', 'Settings saved', 'success');
            // Apply the new theme
            applyTheme(settings.ui_theme);
        } else {
            showToast('Error', 'Failed to save settings', 'error');
        }
    } catch (error) {
        console.error('Error saving settings:', error);
        showToast('Error', 'Failed to save settings', 'error');
    }
}

async function testTelegram() {
    const btn = document.getElementById('testTelegramBtn');
    btn.innerHTML = '<span class="spinner"></span> Testing...';
    btn.disabled = true;
    
    try {
        const response = await fetch('/api/telegram/test', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                bot_token: document.getElementById('telegramBotToken').value,
                chat_id: document.getElementById('telegramChatId').value
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            showToast('Success', data.message, 'success');
        } else {
            showToast('Error', data.message, 'error');
        }
    } catch (error) {
        console.error('Error testing Telegram:', error);
        showToast('Error', 'Failed to test Telegram connection', 'error');
    } finally {
        btn.innerHTML = '<i class="fab fa-telegram"></i> Test Connection';
        btn.disabled = false;
    }
}

// ==================== SYSLOG DIAGNOSTICS ====================

async function loadSyslogDiagnostics() {
    const loadingEl = document.getElementById('syslogDiagnosticsLoading');
    const contentEl = document.getElementById('syslogDiagnosticsContent');
    const errorEl = document.getElementById('syslogDiagnosticsError');
    
    if (!loadingEl || !contentEl || !errorEl) return;
    
    loadingEl.style.display = 'block';
    contentEl.style.display = 'none';
    errorEl.style.display = 'none';
    
    try {
        const response = await fetch('/api/syslog/diagnostics');
        
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        
        const data = await response.json();
        
        if (data.error) {
            throw new Error(data.error);
        }
        
        // Check for port conflicts or issues
        const warningEl = document.getElementById('portConflictWarning');
        const warningMsgEl = document.getElementById('portConflictMessage');
        if (warningEl && warningMsgEl) {
            let warnings = [];
            if (!data.receiver_running) {
                warnings.push('Syslog receiver is not running.');
            }
            if (data.receiver_running && !data.udp_bound) {
                warnings.push(`UDP port ${data.udp_port} could not be bound.`);
            }
            if (data.receiver_running && !data.tcp_bound) {
                warnings.push(`TCP port ${data.tcp_port} could not be bound.`);
            }
            
            if (warnings.length > 0) {
                warningMsgEl.textContent = ' ' + warnings.join(' ');
                warningEl.style.display = 'block';
            } else {
                warningEl.style.display = 'none';
            }
        }
        
        // Update summary stats
        document.getElementById('receiverStatus').innerHTML = data.receiver_running 
            ? '<span class="status-dot"></span> Running' 
            : '<span class="status-dot offline"></span> Stopped';
        document.getElementById('receiverUptime').textContent = formatUptime(data.uptime_seconds);
        
        // Show port status with bound indicator
        const udpStatus = data.udp_bound ? '✓' : '✗';
        const tcpStatus = data.tcp_bound ? '✓' : '✗';
        document.getElementById('udpPort').innerHTML = `${data.udp_port || '--'} <small style="color: ${data.udp_bound ? '#28a745' : '#dc3545'};">${udpStatus}</small>`;
        document.getElementById('tcpPort').innerHTML = `${data.tcp_port || '--'} <small style="color: ${data.tcp_bound ? '#28a745' : '#dc3545'};">${tcpStatus}</small>`;
        
        document.getElementById('totalClients').textContent = data.total_clients || 0;
        document.getElementById('activeClients').textContent = data.active_clients || 0;
        
        const issuesEl = document.getElementById('clientsWithIssues');
        const issueCount = data.clients_with_issues || 0;
        issuesEl.textContent = issueCount;
        issuesEl.style.color = issueCount > 0 ? '#dc3545' : '#28a745';
        
        // Render clients table
        const noClientsEl = document.getElementById('noClientsMessage');
        const tableEl = document.getElementById('clientsTable');
        const tbodyEl = document.getElementById('clientsTableBody');
        
        if (!data.clients || data.clients.length === 0) {
            noClientsEl.style.display = 'block';
            tableEl.style.display = 'none';
        } else {
            noClientsEl.style.display = 'none';
            tableEl.style.display = 'block';
            
            tbodyEl.innerHTML = data.clients.map(client => {
                const statusClass = client.status === 'active' ? 'success' : 
                                   client.status === 'idle' ? 'warning' : 'error';
                const statusIcon = client.status === 'active' ? 'check-circle' : 
                                  client.status === 'idle' ? 'clock' : 'exclamation-circle';
                
                const protocols = client.protocols.join(', ');
                const lastActivity = formatTimeAgo(client.seconds_since_last);
                const issues = client.issues && client.issues.length > 0 
                    ? `<span class="badge badge-danger" title="${client.issues.join('\\n')}">${client.issues.length} issue(s)</span>` 
                    : '<span class="badge badge-success">None</span>';
                
                return `
                    <tr>
                        <td>
                            <span class="status-indicator ${statusClass}">
                                <i class="fas fa-${statusIcon}"></i> ${client.status}
                            </span>
                        </td>
                        <td>
                            <strong>${escapeHtml(client.hostname)}</strong>
                            ${client.hostname !== client.ip ? `<br><small style="color: #666;">${escapeHtml(client.ip)}</small>` : ''}
                        </td>
                        <td><span class="badge">${protocols}</span></td>
                        <td>${client.message_count.toLocaleString()}</td>
                        <td>${client.messages_per_minute}/min</td>
                        <td title="${new Date(client.last_seen).toLocaleString()}">${lastActivity}</td>
                        <td>${issues}</td>
                    </tr>
                `;
            }).join('');
        }
        
        loadingEl.style.display = 'none';
        contentEl.style.display = 'block';
        
    } catch (error) {
        console.error('Error loading syslog diagnostics:', error);
        loadingEl.style.display = 'none';
        errorEl.style.display = 'block';
        document.getElementById('syslogDiagnosticsErrorMsg').textContent = 
            `Failed to load diagnostics: ${error.message}`;
    }
}

async function sendTestSyslog() {
    try {
        const response = await fetch('/api/syslog/test-receive', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        
        const data = await response.json();
        
        if (data.success) {
            showToast('Test Sent', data.message, 'success');
            // Refresh diagnostics after a short delay
            setTimeout(() => loadSyslogDiagnostics(), 1000);
        } else {
            showToast('Error', data.message, 'error');
        }
    } catch (error) {
        console.error('Error sending test syslog:', error);
        showToast('Error', 'Failed to send test syslog message', 'error');
    }
}

function formatUptime(seconds) {
    if (!seconds || seconds < 0) return '--';
    
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    
    if (days > 0) {
        return `${days}d ${hours}h ${minutes}m`;
    } else if (hours > 0) {
        return `${hours}h ${minutes}m`;
    } else if (minutes > 0) {
        return `${minutes}m`;
    } else {
        return `${seconds}s`;
    }
}

function formatTimeAgo(seconds) {
    if (!seconds || seconds < 0) return 'never';
    
    if (seconds < 60) return `${seconds}s ago`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
    return `${Math.floor(seconds / 86400)}d ago`;
}

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

let _loadOllamaModelsPromise = null;

async function loadOllamaModels(customHost = null, force = false) {
    // If there's an in-flight call, return the same promise to deduplicate concurrent calls
    if (_loadOllamaModelsPromise) return _loadOllamaModelsPromise;

    _loadOllamaModelsPromise = (async () => {
        try {
            // Use custom host if provided, otherwise use the value from the input field
            const hostInput = document.getElementById('ollamaHost');
            const host = customHost || (hostInput ? hostInput.value : null);
            
            let url = '/api/ollama/status';
            if (host) {
                url += `?host=${encodeURIComponent(host)}`;
            }
            if (force) {
                url += (url.includes('?') ? '&force=1' : '?force=1');
            }
            
            const response = await fetch(url);
            const data = await response.json();
            
            const select = document.getElementById('ollamaModel');
            const statusEl = document.getElementById('ollamaConnectionStatus');
            
            if (statusEl) {
                if (data.available) {
                    statusEl.innerHTML = `<span class="status-dot"></span> Connected to ${data.host || host}`;
                    statusEl.className = 'status-indicator';
                } else {
                    statusEl.innerHTML = `<span class="status-dot offline"></span> Not connected${data.error ? ': ' + data.error : ''}`;
                    statusEl.className = 'status-indicator';
                }
            }
        
        if (select && data.models && data.models.length > 0) {
            const currentValue = select.value || data.current_model;
            select.innerHTML = data.models.map(model => 
                `<option value="${model}" ${model === currentValue ? 'selected' : ''}>${model}</option>`
            ).join('');
        } else if (select) {
            select.innerHTML = '<option value="">No models available</option>';
        }
        
        return data;
    } catch (error) {
        console.error('Error loading Ollama models:', error);
        const statusEl = document.getElementById('ollamaConnectionStatus');
        if (statusEl) {
            statusEl.innerHTML = `<span class="status-dot offline"></span> Connection error`;
        }
        return { available: false, models: [] };
        } finally {
            _loadOllamaModelsPromise = null;
        }
    })();

    return _loadOllamaModelsPromise;
}

// Modal helpers
function openModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) modal.classList.add('active');
}

function closeModal(modalId) {
    const modal = document.getElementById(modalId);
    if (modal) modal.classList.remove('active');
}

// Toast notifications
function showToast(title, message, type = 'info') {
    const container = document.getElementById('toastContainer') || createToastContainer();
    
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerHTML = `
        <div>
            <strong>${escapeHtml(title)}</strong>
            <p style="margin: 0; font-size: 0.85rem;">${escapeHtml(message)}</p>
        </div>
        <button onclick="this.parentElement.remove()" style="background: none; border: none; cursor: pointer; font-size: 1.2rem;">&times;</button>
    `;
    
    container.appendChild(toast);
    
    setTimeout(() => toast.remove(), 5000);
}

function createToastContainer() {
    const container = document.createElement('div');
    container.id = 'toastContainer';
    container.className = 'toast-container';
    document.body.appendChild(container);
    return container;
}

// Utility functions
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Filter form submit
function submitFilterForm(event) {
    event.preventDefault();
    
    const formData = {
        name: document.getElementById('filterName').value,
        conditions: {
            severity: document.getElementById('filterSeverity').value || undefined,
            source_contains: document.getElementById('filterSourceContains').value || undefined,
            message_contains: document.getElementById('filterMessageContains').value || undefined,
            message_regex: document.getElementById('filterMessageRegex').value || undefined
        },
        notify_telegram: document.getElementById('filterNotifyTelegram').checked,
        enabled: document.getElementById('filterEnabled').checked
    };
    
    // Clean up empty conditions
    Object.keys(formData.conditions).forEach(key => {
        if (!formData.conditions[key]) delete formData.conditions[key];
    });
    
    const filterId = document.getElementById('filterId').value;
    if (filterId) {
        updateFilter(filterId, formData);
    } else {
        createFilter(formData);
    }
}

// Apply log filters
function applyLogFilters() {
    const host = document.getElementById('filterHost')?.value || '';
    const severity = document.getElementById('filterSeveritySelect')?.value || '';
    const search = document.getElementById('filterSearch')?.value?.trim() || '';
    
    // Store active filters for WebSocket log filtering
    activeFilters.host = host;
    activeFilters.severity = severity;
    activeFilters.search = search;
    
    const params = {};
    if (host) params.host = host;  // Use host param for hostname filtering
    if (severity) params.severity = severity;
    if (search) params.search = search;
    
    fetchLogs(params);
}

// Clear all filters
function clearFilters() {
    // Reset dropdowns and search
    const hostEl = document.getElementById('filterHost');
    const severityEl = document.getElementById('filterSeveritySelect');
    const searchEl = document.getElementById('filterSearch');
    
    if (hostEl) hostEl.value = '';
    if (severityEl) severityEl.value = '';
    if (searchEl) searchEl.value = '';
    
    // Clear active filters
    activeFilters.host = '';
    activeFilters.severity = '';
    activeFilters.search = '';
    
    // Fetch all logs
    fetchLogs();
}

// Load hide duplicates default setting and apply to checkbox
async function loadHideDuplicatesDefault() {
    try {
        const response = await fetch('/api/settings');
        const settings = await response.json();
        
        const hideDuplicatesCheckbox = document.getElementById('hideDuplicates');
        if (hideDuplicatesCheckbox && settings.hide_duplicates_default === true) {
            hideDuplicatesCheckbox.checked = true;
            state.hideDuplicates = true;
        }
        
        // Also apply theme from settings if not in localStorage
        if (settings.ui_theme && !localStorage.getItem('ui_theme')) {
            applyTheme(settings.ui_theme);
        }
    } catch (error) {
        console.error('Error loading hide duplicates setting:', error);
    }
}

// Initialize page
document.addEventListener('DOMContentLoaded', () => {
    // Apply saved theme immediately and sync the toggle button
    const savedTheme = localStorage.getItem('ui_theme') || 'default';
    applyTheme(savedTheme);
    updateThemeToggleBtn(savedTheme);
    
    initSocket();
    fetchStats();
    
    // Determine current page and load appropriate data
    const path = window.location.pathname;
    
    // Set a current page value so render logic can adapt (e.g., show limited logs on analysis page)
    state.currentPage = 'dashboard';

    if (path === '/' || path === '/index') {
        state.currentPage = 'dashboard';
        // Dashboard - load hide duplicates setting first, then fetch logs
        loadHideDuplicatesDefault().then(() => {
            fetchLogs({ limit: 10 });
        });
        fetchAlerts();
    } else if (path === '/logs') {
        state.currentPage = 'logs';
        // Reset active filters on page load
        activeFilters.host = '';
        activeFilters.severity = '';
        activeFilters.search = '';
        // Load hide duplicates setting first, then fetch logs
        loadHideDuplicatesDefault().then(() => {
            fetchLogs();
        });
        loadHosts();
    } else if (path === '/filters') {
        state.currentPage = 'filters';
        fetchFilters();
    } else if (path === '/alerts') {
        state.currentPage = 'alerts';
        fetchAlerts();
    } else if (path === '/docker') {
        state.currentPage = 'docker';
        fetchContainers();
    } else if (path === '/analysis') {
        state.currentPage = 'analysis';
        // Load hide duplicates setting first, then fetch logs
        loadHideDuplicatesDefault().then(() => {
            fetchLogs({ limit: 20 });
        });
    } else if (path === '/settings') {
        state.currentPage = 'settings';
        loadSettings();
    }
    
    // Refresh stats periodically
    setInterval(fetchStats, 30000);
});

// Load hosts for filter dropdown
async function loadHosts() {
    try {
        const response = await fetch('/api/hosts');
        const hosts = await response.json();
        
        // Filter to only show FQDNs (hostnames with dots) and exclude short names
        // If a short name has a corresponding FQDN, only show the FQDN
        const fqdnHosts = hosts.filter(h => {
            // If hostname contains a dot, it's likely a FQDN
            if (h.includes('.')) return true;
            // If it's a short name, check if there's a FQDN version
            const hasFqdn = hosts.some(other => other.startsWith(h + '.'));
            return !hasFqdn;  // Only include short name if no FQDN exists
        });
        
        const select = document.getElementById('filterHost');
        if (select) {
            // Preserve current selection
            const currentValue = select.value;
            
            select.innerHTML = '<option value="">All Hosts</option>' +
                fqdnHosts.map(h => `<option value="${h}">${h}</option>`).join('');
            
            // Restore selection if it still exists in the list
            if (currentValue && fqdnHosts.includes(currentValue)) {
                select.value = currentValue;
            }
        }
    } catch (error) {
        console.error('Error loading hosts:', error);
    }
}

// Legacy function for backward compatibility
async function loadSources() {
    return loadHosts();
}

// Chat input enter key handler
document.addEventListener('keypress', (e) => {
    if (e.target.id === 'chatInput' && e.key === 'Enter') {
        sendChatMessage();
    }
});

// Sidebar toggle function
function toggleSidebar() {
    const sidebar = document.querySelector('.sidebar');
    if (sidebar) {
        sidebar.classList.toggle('collapsed');
        localStorage.setItem('sidebar_collapsed', sidebar.classList.contains('collapsed'));
    }
}

// Restore sidebar state on page load
(function() {
    const collapsed = localStorage.getItem('sidebar_collapsed') === 'true';
    if (collapsed) {
        document.addEventListener('DOMContentLoaded', () => {
            const sidebar = document.querySelector('.sidebar');
            if (sidebar) sidebar.classList.add('collapsed');
        });
    }
})();

// Keyboard shortcuts
let keySequence = '';
let keySequenceTimer = null;

document.addEventListener('keydown', (e) => {
    // Ignore if typing in input/textarea
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.isContentEditable) {
        return;
    }
    
    // Ctrl+B: Toggle sidebar
    if (e.ctrlKey && e.key === 'b') {
        e.preventDefault();
        toggleSidebar();
        return;
    }
    
    // Escape: Close modals or clear sequence
    if (e.key === 'Escape') {
        keySequence = '';
        // Close any open modals
        document.querySelectorAll('.modal.show, .chat-container.visible').forEach(el => {
            el.classList.remove('show', 'visible');
        });
        const shortcutsModal = document.getElementById('shortcutsModal');
        if (shortcutsModal) shortcutsModal.style.display = 'none';
        return;
    }
    
    // ? key: Show shortcuts help
    if (e.key === '?') {
        e.preventDefault();
        showShortcutsHelp();
        return;
    }
    
    // Build key sequence for g+letter shortcuts
    clearTimeout(keySequenceTimer);
    keySequence += e.key.toLowerCase();
    
    // Reset sequence after 1 second
    keySequenceTimer = setTimeout(() => {
        keySequence = '';
    }, 1000);
    
    // Navigation shortcuts (g + letter)
    const shortcuts = {
        'gh': '/',           // g h = Home/Dashboard
        'gd': '/',           // g d = Dashboard
        'gl': '/logs',       // g l = Logs
        'gk': '/docker',     // g k = Docker (K for Kontainer)
        'gf': '/filters',    // g f = Filters
        'ga': '/alerts',     // g a = Alerts
        'gi': '/analysis',   // g i = AI Analysis
        'gy': '/ai-history', // g y = AI History
        'gs': '/settings',   // g s = Settings
        'gu': '/users',      // g u = Users
        'gb': '/about'       // g b = About
    };
    
    if (shortcuts[keySequence]) {
        e.preventDefault();
        window.location.href = shortcuts[keySequence];
        keySequence = '';
        return;
    }
    
    // Single key shortcuts
    if (keySequence.length === 1) {
        switch (keySequence) {
            case 't': // Toggle AI Chat
                const chatContainer = document.querySelector('.chat-container');
                if (chatContainer) {
                    chatContainer.classList.toggle('visible');
                    if (chatContainer.classList.contains('visible')) {
                        const chatInput = document.getElementById('chatInput');
                        if (chatInput) chatInput.focus();
                    }
                }
                keySequence = '';
                break;
            case 'm': // Toggle sidebar (m for menu)
                toggleSidebar();
                keySequence = '';
                break;
            case 'r': // Refresh data
                fetchStats();
                if (typeof fetchLogs === 'function') fetchLogs();
                showToast('Refreshed', 'Data has been refreshed', 'info');
                keySequence = '';
                break;
        }
    }
    
    // Clear if sequence is too long
    if (keySequence.length > 2) {
        keySequence = '';
    }
});

// Show keyboard shortcuts help modal
function showShortcutsHelp() {
    let modal = document.getElementById('shortcutsModal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'shortcutsModal';
        modal.className = 'shortcuts-modal';
        modal.innerHTML = `
            <div class="shortcuts-content">
                <div class="shortcuts-header">
                    <h3><i class="fas fa-keyboard"></i> Keyboard Shortcuts</h3>
                    <button onclick="document.getElementById('shortcutsModal').style.display='none'" class="close-btn">&times;</button>
                </div>
                <div class="shortcuts-body" style="display: flex; gap: 2rem;">
                    <div class="shortcut-section" style="flex: 1;">
                        <h4>Navigation (press g then letter)</h4>
                        <div class="shortcut-row"><kbd>g</kbd> <kbd>h</kbd> / <kbd>g</kbd> <kbd>d</kbd> <span>Dashboard</span></div>
                        <div class="shortcut-row"><kbd>g</kbd> <kbd>l</kbd> <span>Logs</span></div>
                        <div class="shortcut-row"><kbd>g</kbd> <kbd>k</kbd> <span>Docker</span></div>
                        <div class="shortcut-row"><kbd>g</kbd> <kbd>f</kbd> <span>Filters</span></div>
                        <div class="shortcut-row"><kbd>g</kbd> <kbd>a</kbd> <span>Alerts</span></div>
                        <div class="shortcut-row"><kbd>g</kbd> <kbd>i</kbd> <span>AI Analysis</span></div>
                        <div class="shortcut-row"><kbd>g</kbd> <kbd>y</kbd> <span>AI History</span></div>
                        <div class="shortcut-row"><kbd>g</kbd> <kbd>s</kbd> <span>Settings</span></div>
                        <div class="shortcut-row"><kbd>g</kbd> <kbd>u</kbd> <span>Users</span></div>
                        <div class="shortcut-row"><kbd>g</kbd> <kbd>b</kbd> <span>About</span></div>
                    </div>
                    <div class="shortcut-section" style="flex: 1;">
                        <h4>Actions</h4>
                        <div class="shortcut-row"><kbd>/</kbd> <span>Focus AI Chat (Analysis page)</span></div>
                        <div class="shortcut-row"><kbd>m</kbd> <span>Toggle Sidebar</span></div>
                        <div class="shortcut-row"><kbd>Ctrl</kbd> <kbd>B</kbd> <span>Toggle Sidebar</span></div>
                        <div class="shortcut-row"><kbd>t</kbd> <span>Toggle AI Chat</span></div>
                        <div class="shortcut-row"><kbd>r</kbd> <span>Refresh Data</span></div>
                        <div class="shortcut-row"><kbd>?</kbd> <span>Show This Help</span></div>
                        <div class="shortcut-row"><kbd>Esc</kbd> <span>Close Modal</span></div>
                    </div>
                </div>
            </div>
        `;
        document.body.appendChild(modal);
    }
    modal.style.display = 'flex';
}
