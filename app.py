# Copyright (C) 2026 Fotios Tsiadimos
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import time
import threading
from datetime import datetime, timezone
from functools import wraps
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
from flask_socketio import SocketIO, emit
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from apscheduler.schedulers.background import BackgroundScheduler
import redis.exceptions
from config import Config
from services.redis_client import redis_client
from services.syslog_receiver import SyslogReceiver
from services.docker_collector import DockerLogCollector
from services.ollama_analyzer import OllamaAnalyzer
from services.telegram_notifier import telegram_notifier

# Initialize Flask app
app = Flask(__name__)
app.config.from_object(Config)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'logai-monitor-secret-key-change-in-production')

# Track Redis connection status
_redis_available = False
_redis_error_message = None

def check_redis_connection():
    """Check if Redis is available and update status"""
    global _redis_available, _redis_error_message
    try:
        if redis_client.ping():
            _redis_available = True
            _redis_error_message = None
            return True
    except redis.exceptions.ConnectionError as e:
        _redis_available = False
        _redis_error_message = str(e)
        print(f"[App] Redis connection error: {e}")
    except Exception as e:
        _redis_available = False
        _redis_error_message = str(e)
        print(f"[App] Redis error: {e}")
    return False

def render_redis_error():
    """Render the Redis connection error page"""
    return render_template('error.html',
        icon='database',
        icon_color='#dc3545',
        title='Redis Connection Failed',
        message='LogRadarAI cannot connect to Redis. Redis is required for storing logs, settings, and user data.',
        details=[
            'Make sure Redis server is running: <code>redis-server</code> or <code>systemctl start redis</code>',
            'Check Redis is listening on the correct port: <code>redis-cli ping</code>',
            f'Current configuration: <code>{Config.REDIS_HOST}:{Config.REDIS_PORT}</code>',
            'If using Docker: <code>docker run -d -p 6379:6379 redis</code>',
            'Check firewall settings if Redis is on a remote host'
        ],
        details_title='How to fix this',
        error_code=_redis_error_message or 'Connection refused',
        show_home_link=False
    ), 503

def require_redis(f):
    """Decorator to check Redis connection before executing route"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        global _redis_available, _redis_error_message
        if not _redis_available:
            if not check_redis_connection():
                return render_redis_error()
        try:
            return f(*args, **kwargs)
        except redis.exceptions.ConnectionError as e:
            _redis_available = False
            _redis_error_message = str(e)
            return render_redis_error()
    return decorated_function

def require_redis_api(f):
    """Decorator for API routes - returns JSON error instead of HTML"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        global _redis_available, _redis_error_message
        if not _redis_available:
            if not check_redis_connection():
                return jsonify({
                    'error': 'Redis connection failed',
                    'message': _redis_error_message or 'Connection refused',
                    'redis_available': False
                }), 503
        try:
            return f(*args, **kwargs)
        except redis.exceptions.ConnectionError as e:
            _redis_available = False
            _redis_error_message = str(e)
            return jsonify({
                'error': 'Redis connection failed',
                'message': str(e),
                'redis_available': False
            }), 503
    return decorated_function

# Initialize Flask-Login
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Please log in to access this page.'
login_manager.login_message_category = 'info'

# User class for Flask-Login
class User(UserMixin):
    def __init__(self, user_data):
        self.id = user_data.get('id')
        self.username = user_data.get('username')
        self.email = user_data.get('email', '')
        self.role = user_data.get('role', 'user')
        self.is_admin = user_data.get('role') == 'admin'

@login_manager.user_loader
def load_user(user_id):
    try:
        user_data = redis_client.get_user(user_id)
        if user_data:
            return User(user_data)
    except redis.exceptions.ConnectionError:
        pass  # Will be handled by route decorators
    except Exception as e:
        print(f"[App] Error loading user: {e}")
    return None

# Initialize SocketIO with threading mode (more compatible)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Initialize services
syslog_receiver = None
docker_collector = None
ollama_analyzer = None
scheduler = None

def log_callback(log_entry):
    """Callback for new log entries"""
    global _redis_available
    try:
        # Store in Redis
        log_id = redis_client.store_log(log_entry)
        log_entry['id'] = log_id
        
        # Emit to connected clients
        socketio.emit('new_log', log_entry)
    except redis.exceptions.ConnectionError as e:
        _redis_available = False
        print(f"[App] Redis connection lost in log_callback: {e}")
        # Still emit to clients even if Redis is down
        socketio.emit('new_log', log_entry)
        return
    
    # Check against filters
    settings = redis_client.get_settings()
    if settings.get('auto_analyze', True):
        filters = redis_client.get_enabled_filters()
        matched = ollama_analyzer.check_log_against_filters(log_entry, filters)
        
        if matched:
            for f in matched:
                # Create alert
                alert_data = {
                    'log_id': log_id,
                    'filter_id': f.get('id'),
                    'filter_name': f.get('name'),
                    'severity': log_entry.get('severity'),
                    'source': log_entry.get('source'),
                    'hostname': log_entry.get('hostname', log_entry.get('source')),
                    'message': log_entry.get('message', '')[:500]
                }
                alert_id = redis_client.store_alert(alert_data)
                
                # Emit alert
                socketio.emit('new_alert', alert_data)
                
                # Send Telegram notification if enabled
                if f.get('notify_telegram', False) and settings.get('telegram_enabled', False):
                    if ollama_analyzer.is_available():
                        alert_msg = ollama_analyzer.generate_alert_message(log_entry, {})
                        # Fallback to original log message if AI returns an empty/whitespace-only string
                        if not alert_msg or not alert_msg.strip():
                            alert_msg = log_entry.get('message', '')[:200]
                    else:
                        alert_msg = log_entry.get('message', '')[:200]
                    
                    telegram_notifier.send_alert(
                        log_entry.get('severity', 'info'),
                        log_entry.get('source', 'unknown'),
                        alert_msg,
                        hostname=log_entry.get('hostname')
                    )

def periodic_analysis():
    """Periodic log analysis task"""
    settings = redis_client.get_settings()
    
    if not settings.get('auto_analyze', True):
        return
    
    if not ollama_analyzer or not ollama_analyzer.is_available():
        return
    
    # Get unanalyzed logs
    logs = redis_client.get_unanalyzed_logs(limit=Config.MAX_LOGS_PER_ANALYSIS)
    
    if not logs:
        return
    
    print(f"[Scheduler] Analyzing {len(logs)} logs...")
    
    # Batch analysis
    result = ollama_analyzer.analyze_logs_batch(logs)
    
    if result.get('success'):
        analysis = result.get('analysis', {})
        
        # Mark logs as analyzed
        for log in logs:
            redis_client.mark_log_analyzed(log['id'], str(analysis))
        
        # Store in AI history
        redis_client.store_analysis_history({
            'type': 'auto',
            'logs_analyzed': len(logs),
            'analysis': analysis
        })
        
        # Send Telegram summary if critical issues found
        if analysis.get('overall_status') == 'critical' and settings.get('telegram_enabled'):
            stats = redis_client.get_stats()
            telegram_notifier.send_summary(stats, analysis)
        
        # Emit analysis result
        socketio.emit('analysis_complete', {
            'logs_analyzed': len(logs),
            'analysis': analysis
        })

def cleanup_task():
    """Periodic cleanup of old logs (improved diagnostics). Uses the configured
    'log_retention_hours' setting when available, otherwise falls back to Config."""
    ts = datetime.now(timezone.utc).isoformat()
    try:
        # Determine retention to use for this run (prefer saved settings)
        try:
            settings = redis_client.get_settings()
            retention = int(settings.get('log_retention_hours', Config.LOG_RETENTION_HOURS))
        except Exception:
            retention = Config.LOG_RETENTION_HOURS

        try:
            app.logger.info(f"[Scheduler] Running cleanup now (will use retention={retention}h)")
        except Exception:
            print(f"[Scheduler] Running cleanup now (will use retention={retention}h)")

        count = redis_client.cleanup_old_logs(retention_hours=retention)
        status = redis_client.get_cleanup_status()
        msg = (f"[Scheduler] Cleanup run at {ts} - removed {count} old logs "
               f"(used_retention={retention}h) | timeline_count={status.get('timeline_count')} "
               f"last_run={status.get('last_run')} last_removed={status.get('last_removed')}")
        try:
            app.logger.info(msg)
        except Exception:
            print(msg)
    except redis.exceptions.ConnectionError as e:
        print(f"[Scheduler] Redis connection error during cleanup: {e}")
    except Exception as e:
        print(f"[Scheduler] Cleanup error: {e}")

def init_services():
    """Initialize all services"""
    global syslog_receiver, docker_collector, ollama_analyzer, scheduler, _redis_available
    
    # Check Redis connection first
    print("[App] Checking Redis connection...")
    if not check_redis_connection():
        print(f"[App] WARNING: Redis is not available! Error: {_redis_error_message}")
        print("[App] Some features will be unavailable until Redis is connected.")
        print("[App] Starting services anyway - Redis can be connected later.")
    else:
        print("[App] Redis connection: OK")
    
    # Initialize Ollama analyzer
    ollama_analyzer = OllamaAnalyzer(cache_ttl=Config.OLLAMA_CACHE_TTL_SECONDS)
    # Prime Ollama availability asynchronously so dashboard shows correct status quickly
    try:
        import threading as _th
        def _prime_ollama():
            try:
                print('[App] Priming Ollama availability check')
                ollama_analyzer.is_available(force_refresh=True)
                print('[App] Ollama availability primed')
            except Exception as _e:
                print(f'[App] Ollama prime failed: {_e}')
        _th.Thread(target=_prime_ollama, daemon=True).start()
    except Exception as _e:
        print(f"[App] Failed to start Ollama prime thread: {_e}")

    # Initialize syslog receiver
    syslog_receiver = SyslogReceiver(callback=log_callback)
    syslog_receiver.start()
    
    # Initialize Docker collector with settings provider
    def safe_get_settings():
        """Wrapper to safely get settings even if Redis is down"""
        try:
            return redis_client.get_settings()
        except redis.exceptions.ConnectionError:
            return redis_client.get_default_settings()
    
    docker_collector = DockerLogCollector(
        callback=log_callback,
        settings_provider=safe_get_settings
    )
    docker_collector.start()
    
    # Initialize Telegram from settings (only if Redis is available)
    if _redis_available:
        try:
            settings = redis_client.get_settings()
            if settings.get('telegram_bot_token') and settings.get('telegram_chat_id'):
                telegram_notifier.configure(
                    settings['telegram_bot_token'],
                    settings['telegram_chat_id']
                )
        except redis.exceptions.ConnectionError:
            print("[App] Could not load Telegram settings - Redis unavailable")
    
    # Initialize scheduler
    scheduler = BackgroundScheduler()
    
    # Get analysis interval (use default if Redis unavailable)
    try:
        settings = redis_client.get_settings() if _redis_available else {}
        analysis_interval = settings.get('analysis_interval', Config.ANALYSIS_INTERVAL_SECONDS)
    except:
        analysis_interval = Config.ANALYSIS_INTERVAL_SECONDS
    
    scheduler.add_job(
        periodic_analysis,
        'interval',
        seconds=analysis_interval
    )
    # Schedule cleanup to run immediately and then every hour
    scheduler.add_job(cleanup_task, 'interval', hours=1, next_run_time=datetime.now(timezone.utc))
    try:
        app.logger.info("[Scheduler] Jobs registered; cleanup scheduled to run immediately")
    except Exception:
        print("[Scheduler] Jobs registered; cleanup scheduled to run immediately")
    
    # Add periodic Redis health check
    def check_redis_health():
        global _redis_available
        was_available = _redis_available
        check_redis_connection()
        if not was_available and _redis_available:
            print("[App] Redis connection restored!")
            # Try to ensure admin exists now that Redis is back
            try:
                redis_client.ensure_admin_exists()
            except:
                pass
        elif was_available and not _redis_available:
            print("[App] Redis connection lost!")
    
    scheduler.add_job(check_redis_health, 'interval', seconds=30)
    scheduler.start()
    try:
        app.logger.info("[Scheduler] started")
    except Exception:
        print("[Scheduler] started")
    
    # Ensure at least one admin user exists (only if Redis is available)
    if _redis_available:
        try:
            redis_client.ensure_admin_exists()
        except redis.exceptions.ConnectionError:
            print("[App] Could not ensure admin exists - Redis unavailable")
    
    print("[App] All services initialized")

# ==================== AUTH ROUTES ====================

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page"""
    # Check Redis before allowing login
    if not _redis_available:
        if not check_redis_connection():
            return render_redis_error()
    
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        remember = request.form.get('remember', False)
        
        try:
            user_data = redis_client.get_user_by_username(username)
            
            if user_data and check_password_hash(user_data.get('password_hash', ''), password):
                user = User(user_data)
                login_user(user, remember=remember)
                
                next_page = request.args.get('next')
                if next_page:
                    return redirect(next_page)
                return redirect(url_for('index'))
            
            flash('Invalid username or password', 'error')
        except redis.exceptions.ConnectionError:
            return render_redis_error()
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    """Logout"""
    logout_user()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))

# ==================== WEB ROUTES ====================

@app.route('/')
@login_required
@require_redis
def index():
    """Main dashboard"""
    return render_template('index.html')

@app.route('/logs')
@login_required
@require_redis
def logs():
    """Logs page"""
    return render_template('logs.html')

@app.route('/filters')
@login_required
@require_redis
def filters():
    """Filters page"""
    return render_template('filters.html')

@app.route('/alerts')
@login_required
@require_redis
def alerts():
    """Alerts page"""
    return render_template('alerts.html')

@app.route('/docker')
@login_required
@require_redis
def docker():
    """Docker containers page"""
    return render_template('docker.html')

@app.route('/analysis')
@login_required
@require_redis
def analysis():
    """AI Analysis page"""
    return render_template('analysis.html')

@app.route('/ai-history')
@login_required
@require_redis
def ai_history():
    """AI Analysis History page"""
    return render_template('ai_history.html')

@app.route('/settings')
@login_required
@require_redis
def settings():
    """Settings page"""
    return render_template('settings.html')

@app.route('/users')
@login_required
@require_redis
def users():
    """User management page (admin only)"""
    if not current_user.is_admin:
        flash('Access denied. Admin privileges required.', 'error')
        return redirect(url_for('index'))
    return render_template('users.html')

@app.route('/about')
@login_required
@require_redis
def about():
    """About page"""
    return render_template('about.html')

# ==================== API ROUTES ====================

@app.route('/api/stats')
@require_redis_api
def api_stats():
    """Get system statistics"""
    stats = redis_client.get_stats()
    stats['redis_connected'] = redis_client.ping()
    # Use cached availability to avoid triggering a list() call on every /api/stats
    if ollama_analyzer:
        stats['ollama_available'] = ollama_analyzer.cached_availability()
        stats['ollama_last_check_age'] = ollama_analyzer.last_check_age()
        # If we have never checked or cache is stale, trigger a background refresh so UI updates quickly
        try:
            last_age = stats['ollama_last_check_age']
            # If never checked or older than TTL and no check in progress, start one
            if (last_age is None or (last_age is not None and last_age > ollama_analyzer._cache_ttl)) and not getattr(ollama_analyzer, '_check_lock').locked():
                import threading as _th
                def _refresh():
                    try:
                        print('[App] Background refresh of Ollama availability')
                        ollama_analyzer.is_available(force_refresh=True)
                        print('[App] Background refresh complete')
                    except Exception as _e:
                        print(f'[App] Background refresh failed: {_e}')
                _th.Thread(target=_refresh, daemon=True).start()
        except Exception as _e:
            print(f"[App] Error scheduling Ollama background refresh: {_e}")
    else:
        stats['ollama_available'] = False
        stats['ollama_last_check_age'] = None
    stats['telegram_enabled'] = telegram_notifier.enabled
    return jsonify(stats)

@app.route('/api/logs')
@require_redis_api
def api_logs():
    """Get logs with optional filtering"""
    limit = request.args.get('limit', 100, type=int)
    offset = request.args.get('offset', 0, type=int)
    source = request.args.get('source')
    host = request.args.get('host')
    severity = request.args.get('severity')
    search = request.args.get('search')
    
    logs = redis_client.get_logs(
        limit=limit,
        offset=offset,
        source=source,
        host=host,
        severity=severity,
        search=search
    )
    
    return jsonify({
        'logs': logs,
        'count': len(logs),
        'total': redis_client.get_logs_count()
    })

@app.route('/api/logs/<log_id>')
@require_redis_api
def api_log_detail(log_id):
    """Get a single log entry"""
    log = redis_client.get_log(log_id)
    if not log:
        return jsonify({'error': 'Log not found'}), 404
    return jsonify(log)

# Clear all logs API
@app.route('/api/logs/clear', methods=['POST'])
@require_redis_api
def api_clear_all_logs():
    """Delete all logs from the database"""
    count = redis_client.clear_all_logs()
    return jsonify({'status': 'ok', 'deleted': count})

# Manual cleanup (admin only)
@app.route('/api/logs/cleanup', methods=['POST'])
@login_required
@require_redis_api
def api_cleanup_old_logs():
    """Trigger immediate cleanup of logs older than retention period (admin only)"""
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    try:
        # Use configured retention if available
        try:
            settings = redis_client.get_settings()
            retention = int(settings.get('log_retention_hours', Config.LOG_RETENTION_HOURS))
        except Exception:
            retention = Config.LOG_RETENTION_HOURS

        count = redis_client.cleanup_old_logs(retention_hours=retention)
        status = redis_client.get_cleanup_status()
        print(f"[Admin] Manual cleanup removed {count} old logs (used_retention={retention}h)")
        return jsonify({
            'status': 'ok',
            'deleted': count,
            'redis_connected': redis_client.ping(),
            'timeline_count': status.get('timeline_count'),
            'retention_hours': retention,
            'cleanup_status': status
        })
    except redis.exceptions.ConnectionError as e:
        return jsonify({'error': 'Redis connection failed', 'message': str(e)}), 503
    except Exception as e:
        return jsonify({'error': 'Cleanup failed', 'message': str(e)}), 500

@app.route('/api/logs/ingest', methods=['POST'])
@require_redis_api
def api_ingest_log():
    """Ingest a log entry via HTTP"""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    log_entry = {
        'source': data.get('source', 'http'),
        'source_type': 'http',
        'severity': data.get('severity', 'info'),
        'message': data.get('message', ''),
        'hostname': data.get('hostname', request.remote_addr),
        'program': data.get('program', 'unknown'),
        'timestamp': data.get('timestamp')
    }
    
    log_callback(log_entry)
    
    return jsonify({'status': 'ok', 'id': log_entry.get('id')})

@app.route('/api/sources')
@require_redis_api
def api_sources():
    """Get unique log sources"""
    return jsonify(redis_client.get_sources())

@app.route('/api/hosts')
@require_redis_api
def api_hosts():
    """Get unique hostnames"""
    return jsonify(redis_client.get_hosts())

@app.route('/api/severities')
@require_redis_api
def api_severities():
    """Get unique severities"""
    return jsonify(redis_client.get_severities())

# Filter API
@app.route('/api/filters', methods=['GET'])
@require_redis_api
def api_get_filters():
    """Get all filters"""
    return jsonify(redis_client.get_filters())

@app.route('/api/filters', methods=['POST'])
@require_redis_api
def api_create_filter():
    """Create a new filter"""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    filter_id = redis_client.create_filter(data)
    return jsonify({'status': 'ok', 'id': filter_id})

@app.route('/api/filters/<filter_id>', methods=['GET'])
@require_redis_api
def api_get_filter(filter_id):
    """Get a single filter"""
    f = redis_client.get_filter(filter_id)
    if not f:
        return jsonify({'error': 'Filter not found'}), 404
    return jsonify(f)

@app.route('/api/filters/<filter_id>', methods=['PUT'])
@require_redis_api
def api_update_filter(filter_id):
    """Update a filter"""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    if redis_client.update_filter(filter_id, data):
        return jsonify({'status': 'ok'})
    return jsonify({'error': 'Filter not found'}), 404

@app.route('/api/filters/<filter_id>', methods=['DELETE'])
@require_redis_api
def api_delete_filter(filter_id):
    """Delete a filter"""
    if redis_client.delete_filter(filter_id):
        return jsonify({'status': 'ok'})
    return jsonify({'error': 'Filter not found'}), 404

# Alert API
@app.route('/api/alerts')
@require_redis_api
def api_get_alerts():
    """Get alerts"""
    limit = request.args.get('limit', 50, type=int)
    acknowledged = request.args.get('acknowledged')
    
    if acknowledged is not None:
        acknowledged = acknowledged.lower() == 'true'
    
    alerts = redis_client.get_alerts(limit=limit, acknowledged=acknowledged)
    return jsonify(alerts)

@app.route('/api/alerts/<alert_id>/acknowledge', methods=['POST'])
@require_redis_api
def api_acknowledge_alert(alert_id):
    """Acknowledge an alert"""
    if redis_client.acknowledge_alert(alert_id):
        return jsonify({'status': 'ok'})
    return jsonify({'error': 'Alert not found'}), 404

@app.route('/api/alerts/acknowledge-all', methods=['POST'])
@require_redis_api
def api_acknowledge_all_alerts():
    """Acknowledge all unacknowledged alerts"""
    count = redis_client.acknowledge_all_alerts()
    return jsonify({'status': 'ok', 'acknowledged': count})

@app.route('/api/alerts/clear', methods=['POST'])
@require_redis_api
def api_clear_alerts():
    """Delete all acknowledged alerts"""
    count = redis_client.clear_acknowledged_alerts()
    return jsonify({'status': 'ok', 'deleted': count})

# Docker API
@app.route('/api/docker/containers')
@require_redis_api
def api_docker_containers():
    """Get Docker containers"""
    if docker_collector:
        return jsonify(docker_collector.get_containers())
    return jsonify([])

@app.route('/api/docker/containers/<container_id>/logs')
@require_redis_api
def api_docker_logs(container_id):
    """Get logs from a specific container"""
    lines = request.args.get('lines', 100, type=int)
    if docker_collector:
        logs = docker_collector.get_container_logs(container_id, lines)
        return jsonify(logs)
    return jsonify([])

# Ollama API
@app.route('/api/ollama/status')
@require_redis_api
def api_ollama_status():
    """Get Ollama status"""
    # Check if a custom host is provided in query params (for testing from settings)
    custom_host = request.args.get('host')
    
    if custom_host:
        # Test with custom host
        import ollama as ollama_lib
        try:
            client = ollama_lib.Client(host=custom_host)
            response = client.list()
            models = [m['name'] for m in response.get('models', [])]
            return jsonify({
                'available': True,
                'models': models,
                'current_model': ollama_analyzer.model if ollama_analyzer else '',
                'host': custom_host
            })
        except Exception as e:
            return jsonify({
                'available': False,
                'models': [],
                'error': str(e),
                'host': custom_host
            })

    # Honor optional force-refresh query parameter to bypass cache
    force = request.args.get('force', '0').lower() in ('1', 'true', 'yes')
    
    # Use configured analyzer
    if ollama_analyzer:
        # Update host from settings
        settings = redis_client.get_settings()
        if settings.get('ollama_host'):
            ollama_analyzer.host = settings['ollama_host']
        if settings.get('ollama_model'):
            ollama_analyzer.model = settings['ollama_model']
            
        available = ollama_analyzer.is_available(force_refresh=force)
        models = ollama_analyzer.get_models(force_refresh=force) if available else []
        return jsonify({
            'available': available,
            'models': models,
            'current_model': ollama_analyzer.model,
            'host': ollama_analyzer.host
        })
    return jsonify({'available': False, 'models': []})

@app.route('/api/ollama/analyze', methods=['POST'])
@require_redis_api
def api_ollama_analyze():
    """Analyze logs with Ollama"""
    data = request.get_json()
    
    if not ollama_analyzer or not ollama_analyzer.is_available():
        return jsonify({'error': 'Ollama not available'}), 503
    
    if 'log_id' in data:
        log = redis_client.get_log(data['log_id'])
        if not log:
            return jsonify({'error': 'Log not found'}), 404
        result = ollama_analyzer.analyze_log(log)
        
        # Store single log analysis in history
        if result.get('success'):
            redis_client.store_analysis_history({
                'type': 'single',
                'logs_analyzed': 1,
                'analysis': result.get('analysis', {}),
                'log_source': log.get('source', 'unknown'),
                'log_severity': log.get('severity', 'info')
            })
    elif 'logs' in data:
        result = ollama_analyzer.analyze_logs_batch(data['logs'])
        
        # Store batch analysis in history
        if result.get('success'):
            redis_client.store_analysis_history({
                'type': 'batch',
                'logs_analyzed': result.get('logs_analyzed', len(data['logs'])),
                'analysis': result.get('analysis', {})
            })
    else:
        return jsonify({'error': 'No log_id or logs provided'}), 400
    
    return jsonify(result)

@app.route('/api/ai-history')
@require_redis_api
def api_ai_history():
    """Get AI analysis history"""
    limit = request.args.get('limit', 50, type=int)
    history = redis_client.get_analysis_history(limit)
    return jsonify(history)

@app.route('/api/ai-history/<history_id>')
@require_redis_api
def api_ai_history_entry(history_id):
    """Get a single AI analysis history entry"""
    entry = redis_client.get_analysis_history_entry(history_id)
    if not entry:
        return jsonify({'error': 'History entry not found'}), 404
    return jsonify(entry)

@app.route('/api/ai-history/<history_id>', methods=['DELETE'])
@require_redis_api
def api_ai_history_delete(history_id):
    """Delete an AI analysis history entry"""
    if redis_client.delete_analysis_history(history_id):
        return jsonify({'status': 'ok'})
    return jsonify({'error': 'History entry not found'}), 404

@app.route('/api/ai-history', methods=['DELETE'])
@require_redis_api
def api_ai_history_clear_all():
    """Clear all AI analysis history"""
    count = redis_client.clear_all_analysis_history()
    return jsonify({'status': 'ok', 'deleted': count})

@app.route('/api/ollama/chat', methods=['POST'])
@require_redis_api
def api_ollama_chat():
    """Chat with AI about logs"""
    data = request.get_json()
    
    if not ollama_analyzer or not ollama_analyzer.is_available():
        return jsonify({'error': 'Ollama not available'}), 503
    
    message = data.get('message', '')
    context = data.get('context', '')
    
    response = ollama_analyzer.chat(message, context)
    return jsonify({'response': response})

# Settings API
@app.route('/api/settings', methods=['GET'])
@require_redis_api
def api_get_settings():
    """Get settings"""
    return jsonify(redis_client.get_settings())

@app.route('/api/settings', methods=['POST'])
@require_redis_api
def api_save_settings():
    """Save settings"""
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    redis_client.save_settings(data)
    
    # Reconfigure services
    if data.get('telegram_bot_token') and data.get('telegram_chat_id'):
        telegram_notifier.configure(
            data['telegram_bot_token'],
            data['telegram_chat_id']
        )
    
    if ollama_analyzer:
        ollama_analyzer.model = data.get('ollama_model', Config.OLLAMA_MODEL)
        ollama_analyzer.host = data.get('ollama_host', Config.OLLAMA_HOST)
    
    return jsonify({'status': 'ok'})

@app.route('/api/telegram/test', methods=['POST'])
@require_redis_api
def api_telegram_test():
    """Test Telegram connection"""
    data = request.get_json() or {}
    
    bot_token = data.get('bot_token') or telegram_notifier.bot_token
    chat_id = data.get('chat_id') or telegram_notifier.chat_id
    
    if bot_token and chat_id:
        telegram_notifier.configure(bot_token, chat_id)
    
    success, message = telegram_notifier.test_connection()
    return jsonify({'success': success, 'message': message})

# ==================== SYSLOG DIAGNOSTICS API ====================

@app.route('/api/syslog/diagnostics')
@login_required
def api_syslog_diagnostics():
    """Get syslog receiver diagnostics and client information"""
    if not syslog_receiver:
        return jsonify({
            'error': 'Syslog receiver not initialized',
            'receiver_running': False,
            'clients': []
        }), 500
    
    diagnostics = syslog_receiver.get_client_diagnostics()
    return jsonify(diagnostics)

@app.route('/api/syslog/test-receive', methods=['POST'])
@login_required
def api_syslog_test_receive():
    """
    Send a test syslog message to verify the receiver is working.
    This sends a UDP message to the local syslog port.
    """
    import socket as test_socket
    
    try:
        test_msg = f"<14>Jan  1 00:00:00 LogRadarAI-test test[9999]: Test message from LogRadarAI diagnostics at {datetime.now(timezone.utc).isoformat()}"
        
        sock = test_socket.socket(test_socket.AF_INET, test_socket.SOCK_DGRAM)
        sock.sendto(test_msg.encode('utf-8'), ('127.0.0.1', Config.SYSLOG_PORT))
        sock.close()
        
        return jsonify({
            'success': True,
            'message': f'Test message sent to UDP port {Config.SYSLOG_PORT}. Check if it appears in recent logs.'
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Failed to send test message: {str(e)}'
        }), 500

# ==================== SOCKETIO EVENTS ====================

@socketio.on('connect')
def handle_connect():
    """Handle client connection"""
    print(f"[SocketIO] Client connected")
    emit('connected', {'status': 'ok'})

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection"""
    print(f"[SocketIO] Client disconnected")

@socketio.on('subscribe_logs')
def handle_subscribe_logs():
    """Subscribe to log updates"""
    print(f"[SocketIO] Client subscribed to logs")

# ==================== USER MANAGEMENT API ====================

@app.route('/api/users', methods=['GET'])
@login_required
@require_redis_api
def api_get_users():
    """Get all users (admin only)"""
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    users = redis_client.get_all_users()
    # Remove password hashes from response
    for user in users:
        user.pop('password_hash', None)
    return jsonify(users)

@app.route('/api/users', methods=['POST'])
@login_required
@require_redis_api
def api_create_user():
    """Create a new user (admin only)"""
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    username = data.get('username', '').strip()
    password = data.get('password', '')
    email = data.get('email', '').strip()
    role = data.get('role', 'user')
    
    if not username or not password:
        return jsonify({'error': 'Username and password are required'}), 400
    
    if len(password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400
    
    # Check if username already exists
    if redis_client.get_user_by_username(username):
        return jsonify({'error': 'Username already exists'}), 400
    
    user_data = {
        'username': username,
        'password_hash': generate_password_hash(password),
        'email': email,
        'role': role
    }
    
    user_id = redis_client.create_user(user_data)
    return jsonify({'status': 'ok', 'id': user_id})

@app.route('/api/users/<user_id>', methods=['PUT'])
@login_required
@require_redis_api
def api_update_user(user_id):
    """Update a user (admin only, or self)"""
    if not current_user.is_admin and current_user.id != user_id:
        return jsonify({'error': 'Access denied'}), 403
    
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400
    
    user = redis_client.get_user(user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    
    # Update allowed fields
    if 'email' in data:
        user['email'] = data['email'].strip()
    
    if 'password' in data and data['password']:
        if len(data['password']) < 6:
            return jsonify({'error': 'Password must be at least 6 characters'}), 400
        user['password_hash'] = generate_password_hash(data['password'])
    
    # Only admin can change role
    if current_user.is_admin and 'role' in data:
        user['role'] = data['role']
    
    redis_client.update_user(user_id, user)
    return jsonify({'status': 'ok'})

@app.route('/api/users/<user_id>', methods=['DELETE'])
@login_required
@require_redis_api
def api_delete_user(user_id):
    """Delete a user (admin only)"""
    if not current_user.is_admin:
        return jsonify({'error': 'Access denied'}), 403
    
    # Prevent deleting yourself
    if current_user.id == user_id:
        return jsonify({'error': 'Cannot delete your own account'}), 400
    
    if redis_client.delete_user(user_id):
        return jsonify({'status': 'ok'})
    return jsonify({'error': 'User not found'}), 404

@app.route('/api/users/current', methods=['GET'])
@login_required
@require_redis_api
def api_current_user():
    """Get current user info"""
    return jsonify({
        'id': current_user.id,
        'username': current_user.username,
        'email': current_user.email,
        'role': current_user.role,
        'is_admin': current_user.is_admin
    })

# ==================== STARTUP ====================

_services_initialized = False

def create_app():
    """Application factory"""
    global _services_initialized
    
    # Ensure templates directory exists
    os.makedirs(os.path.join(app.root_path, 'templates'), exist_ok=True)
    os.makedirs(os.path.join(app.root_path, 'static', 'css'), exist_ok=True)
    os.makedirs(os.path.join(app.root_path, 'static', 'js'), exist_ok=True)
    
    # Initialize services once (for flask run)
    if not _services_initialized:
        # In debug mode, only initialize in the reloader child process (WERKZEUG_RUN_MAIN)
        if Config.DEBUG and os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
            print("[App] Debug reloader detected - delaying service initialization to child process")
        else:
            init_services()
            _services_initialized = True
    
    return app

# Auto-initialize services when module is loaded (for flask run)
with app.app_context():
    if not _services_initialized:
        # In debug mode, only initialize in the reloader child process (WERKZEUG_RUN_MAIN)
        if Config.DEBUG and os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
            print("[App] Debug reloader detected - delaying service initialization to child process")
        else:
            init_services()
            _services_initialized = True

if __name__ == '__main__':
    # Run the app with socketio
    socketio.run(
        app,
        host='0.0.0.0',
        port=5059,
        debug=Config.DEBUG,
        allow_unsafe_werkzeug=True
    )
