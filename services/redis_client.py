# Copyright (C) 2026 Fotios Tsiadimos
# SPDX-License-Identifier: GPL-3.0-or-later

import redis
import json
import time
from datetime import datetime, timezone
from typing import List, Dict, Optional, Any
from config import Config

class RedisClient:
    """Redis client for log storage and filter management"""
    
    def __init__(self):
        self.client = redis.Redis(
            host=Config.REDIS_HOST,
            port=Config.REDIS_PORT,
            db=Config.REDIS_DB,
            password=Config.REDIS_PASSWORD,
            decode_responses=True
        )
        
    def ping(self) -> bool:
        """Check Redis connection"""
        try:
            return self.client.ping()
        except:
            return False
    
    # ==================== LOG OPERATIONS ====================
    
    def store_log(self, log_entry: Dict) -> str:
        """Store a log entry"""
        log_id = f"log:{int(time.time() * 1000000)}"
        log_entry['id'] = log_id
        log_entry['timestamp'] = log_entry.get('timestamp', datetime.now(timezone.utc).isoformat())
        log_entry['analyzed'] = False
        
        # Store the log
        self.client.hset(log_id, mapping={k: json.dumps(v) if isinstance(v, (dict, list, bool)) else str(v) for k, v in log_entry.items()})
        
        # Add to sorted set for time-based queries
        self.client.zadd('logs:timeline', {log_id: time.time()})
        
        # Index by source
        source = log_entry.get('source', 'unknown')
        self.client.sadd(f'logs:source:{source}', log_id)
        
        # Index by hostname
        hostname = log_entry.get('hostname', log_entry.get('source', 'unknown'))
        self.client.sadd(f'logs:host:{hostname}', log_id)
        
        # Index by severity
        severity = log_entry.get('severity', 'info')
        self.client.sadd(f'logs:severity:{severity}', log_id)
        
        # Set TTL for automatic cleanup
        ttl_seconds = Config.LOG_RETENTION_HOURS * 3600
        self.client.expire(log_id, ttl_seconds)
        
        return log_id
    
    def get_log(self, log_id: str) -> Optional[Dict]:
        """Get a single log entry"""
        data = self.client.hgetall(log_id)
        if not data:
            return None
        
        # Parse JSON fields
        for key in ['analyzed']:
            if key in data:
                try:
                    data[key] = json.loads(data[key])
                except:
                    pass
        return data
    
    def get_logs(self, limit: int = 100, offset: int = 0, source: str = None, 
                 severity: str = None, search: str = None, start_time: float = None,
                 end_time: float = None, host: str = None) -> List[Dict]:
        """Get logs with optional filtering"""
        
        # Get log IDs from timeline (newest first)
        if start_time and end_time:
            log_ids = self.client.zrevrangebyscore('logs:timeline', end_time, start_time)
        else:
            log_ids = self.client.zrevrange('logs:timeline', 0, -1)
        
        # Filter by host (hostname)
        if host and host.strip():
            host_logs = self.client.smembers(f'logs:host:{host}')
            log_ids = [lid for lid in log_ids if lid in host_logs]
        
        # Filter by source
        if source and source.strip():
            source_logs = self.client.smembers(f'logs:source:{source}')
            log_ids = [lid for lid in log_ids if lid in source_logs]
        
        # Filter by severity
        if severity and severity.strip():
            severity_logs = self.client.smembers(f'logs:severity:{severity}')
            log_ids = [lid for lid in log_ids if lid in severity_logs]
        
        # Get log entries
        logs = []
        for log_id in log_ids:
            log = self.get_log(log_id)
            if log:
                # Search filter
                if search and search.strip():
                    message = log.get('message', '').lower()
                    source_str = log.get('source', '').lower()
                    hostname = log.get('hostname', '').lower()
                    program = log.get('program', '').lower()
                    search_lower = search.lower().strip()
                    if search_lower not in message and search_lower not in source_str and search_lower not in hostname and search_lower not in program:
                        continue
                logs.append(log)
        
        # Apply pagination
        return logs[offset:offset + limit]
    
    def get_logs_count(self) -> int:
        """Get total log count"""
        return self.client.zcard('logs:timeline')
    
    def get_unanalyzed_logs(self, limit: int = 100) -> List[Dict]:
        """Get logs that haven't been analyzed yet"""
        log_ids = self.client.zrevrange('logs:timeline', 0, limit * 2)
        logs = []
        for log_id in log_ids:
            log = self.get_log(log_id)
            if log and not log.get('analyzed'):
                logs.append(log)
                if len(logs) >= limit:
                    break
        return logs
    
    def mark_log_analyzed(self, log_id: str, analysis: str):
        """Mark a log as analyzed and store the analysis"""
        self.client.hset(log_id, 'analyzed', 'true')
        self.client.hset(log_id, 'analysis', analysis)
    
    def get_sources(self) -> List[str]:
        """Get all unique log sources"""
        keys = self.client.keys('logs:source:*')
        return [k.replace('logs:source:', '') for k in keys]
    
    def get_severities(self) -> List[str]:
        """Get all unique severities"""
        keys = self.client.keys('logs:severity:*')
        return [k.replace('logs:severity:', '') for k in keys]
    
    def get_hosts(self) -> List[str]:
        """Get all unique hostnames"""
        keys = self.client.keys('logs:host:*')
        return sorted([k.replace('logs:host:', '') for k in keys])
    
    def cleanup_old_logs(self):
        """Remove logs older than retention period"""
        cutoff = time.time() - (Config.LOG_RETENTION_HOURS * 3600)
        old_logs = self.client.zrangebyscore('logs:timeline', 0, cutoff)
        
        for log_id in old_logs:
            log = self.get_log(log_id)
            if log:
                source = log.get('source', 'unknown')
                severity = log.get('severity', 'info')
                hostname = log.get('hostname', log.get('source', 'unknown'))
                self.client.srem(f'logs:source:{source}', log_id)
                self.client.srem(f'logs:severity:{severity}', log_id)
                self.client.srem(f'logs:host:{hostname}', log_id)
            self.client.delete(log_id)
            self.client.zrem('logs:timeline', log_id)
        
        return len(old_logs)
    
    # ==================== FILTER OPERATIONS ====================
    
    def create_filter(self, filter_data: Dict) -> str:
        """Create a new filter"""
        filter_id = f"filter:{int(time.time() * 1000)}"
        filter_data['id'] = filter_id
        filter_data['created_at'] = datetime.now(timezone.utc).isoformat()
        filter_data['enabled'] = filter_data.get('enabled', True)
        filter_data['notify_telegram'] = filter_data.get('notify_telegram', False)
        
        self.client.hset(filter_id, mapping={k: json.dumps(v) if isinstance(v, (dict, list, bool)) else str(v) for k, v in filter_data.items()})
        self.client.sadd('filters:all', filter_id)
        
        return filter_id
    
    def get_filter(self, filter_id: str) -> Optional[Dict]:
        """Get a single filter"""
        data = self.client.hgetall(filter_id)
        if not data:
            return None
        
        # Parse JSON fields (including booleans stored as JSON)
        for key in ['conditions', 'enabled', 'notify_telegram']:
            if key in data:
                try:
                    data[key] = json.loads(data[key])
                except:
                    pass
        return data
    
    def get_filters(self) -> List[Dict]:
        """Get all filters"""
        filter_ids = self.client.smembers('filters:all')
        filters = []
        for filter_id in filter_ids:
            f = self.get_filter(filter_id)
            if f:
                filters.append(f)
        return sorted(filters, key=lambda x: x.get('created_at', ''), reverse=True)
    
    def update_filter(self, filter_id: str, filter_data: Dict) -> bool:
        """Update a filter"""
        if not self.client.exists(filter_id):
            return False
        
        filter_data['updated_at'] = datetime.now(timezone.utc).isoformat()
        self.client.hset(filter_id, mapping={k: json.dumps(v) if isinstance(v, (dict, list, bool)) else str(v) for k, v in filter_data.items()})
        return True
    
    def delete_filter(self, filter_id: str) -> bool:
        """Delete a filter"""
        if not self.client.exists(filter_id):
            return False
        
        self.client.delete(filter_id)
        self.client.srem('filters:all', filter_id)
        return True
    
    def get_enabled_filters(self) -> List[Dict]:
        """Get all enabled filters"""
        return [f for f in self.get_filters() if f.get('enabled')]
    
    # ==================== ALERT OPERATIONS ====================
    
    def store_alert(self, alert_data: Dict) -> str:
        """Store an alert"""
        alert_id = f"alert:{int(time.time() * 1000)}"
        alert_data['id'] = alert_id
        alert_data['timestamp'] = datetime.now(timezone.utc).isoformat()
        alert_data['acknowledged'] = False
        
        self.client.hset(alert_id, mapping={k: json.dumps(v) if isinstance(v, (dict, list, bool)) else str(v) for k, v in alert_data.items()})
        self.client.zadd('alerts:timeline', {alert_id: time.time()})
        
        # Keep alerts for 30 days
        self.client.expire(alert_id, 30 * 24 * 3600)
        
        return alert_id
    
    def get_alerts(self, limit: int = 50, acknowledged: bool = None) -> List[Dict]:
        """Get alerts"""
        alert_ids = self.client.zrevrange('alerts:timeline', 0, limit * 2)
        alerts = []
        for alert_id in alert_ids:
            data = self.client.hgetall(alert_id)
            if data:
                for key in ['acknowledged']:
                    if key in data:
                        try:
                            data[key] = json.loads(data[key])
                        except:
                            pass
                
                if acknowledged is None or data.get('acknowledged') == acknowledged:
                    alerts.append(data)
                    if len(alerts) >= limit:
                        break
        return alerts
    
    def acknowledge_alert(self, alert_id: str) -> bool:
        """Acknowledge an alert"""
        if not self.client.exists(alert_id):
            return False
        self.client.hset(alert_id, 'acknowledged', 'true')
        return True
    
    def acknowledge_all_alerts(self) -> int:
        """Acknowledge all unacknowledged alerts"""
        alert_ids = self.client.zrevrange('alerts:timeline', 0, -1)
        count = 0
        for alert_id in alert_ids:
            data = self.client.hgetall(alert_id)
            if data:
                try:
                    acked = json.loads(data.get('acknowledged', 'false'))
                except:
                    acked = data.get('acknowledged') == 'true'
                if not acked:
                    self.client.hset(alert_id, 'acknowledged', 'true')
                    count += 1
        return count
    
    def clear_acknowledged_alerts(self) -> int:
        """Delete all acknowledged alerts"""
        alert_ids = self.client.zrevrange('alerts:timeline', 0, -1)
        count = 0
        for alert_id in alert_ids:
            data = self.client.hgetall(alert_id)
            if data:
                try:
                    acked = json.loads(data.get('acknowledged', 'false'))
                except:
                    acked = data.get('acknowledged') == 'true'
                if acked:
                    self.client.delete(alert_id)
                    self.client.zrem('alerts:timeline', alert_id)
                    count += 1
        return count
    
    # ==================== SETTINGS OPERATIONS ====================
    
    def get_settings(self) -> Dict:
        """Get application settings"""
        data = self.client.hgetall('settings')
        if not data:
            return self.get_default_settings()
        
        for key in data:
            try:
                data[key] = json.loads(data[key])
            except:
                pass
        return data
    
    def save_settings(self, settings: Dict):
        """Save application settings"""
        self.client.hset('settings', mapping={k: json.dumps(v) for k, v in settings.items()})
    
    def get_default_settings(self) -> Dict:
        """Get default settings"""
        return {
            'telegram_enabled': False,
            'telegram_bot_token': '',
            'telegram_chat_id': '',
            'ollama_enabled': True,
            'ollama_host': Config.OLLAMA_HOST,
            'ollama_model': Config.OLLAMA_MODEL,
            'analysis_interval': Config.ANALYSIS_INTERVAL_SECONDS,
            'log_retention_hours': Config.LOG_RETENTION_HOURS,
            'auto_analyze': True,
            'alert_on_critical': True,
            'alert_on_error': True,
            'docker_enabled': True,
            'docker_excluded_containers': [],
            'hide_duplicates_default': False
        }
    
    # ==================== AI HISTORY OPERATIONS ====================
    
    def store_analysis_history(self, analysis_data: Dict) -> str:
        """Store an AI analysis history entry"""
        history_id = f"ai_history:{int(time.time() * 1000)}"
        analysis_data['id'] = history_id
        analysis_data['timestamp'] = datetime.now(timezone.utc).isoformat()
        
        self.client.hset(history_id, mapping={k: json.dumps(v) if isinstance(v, (dict, list, bool)) else str(v) for k, v in analysis_data.items()})
        self.client.zadd('ai_history:timeline', {history_id: time.time()})
        
        # Keep history for 30 days
        self.client.expire(history_id, 30 * 24 * 3600)
        
        return history_id
    
    def get_analysis_history(self, limit: int = 50) -> List[Dict]:
        """Get AI analysis history"""
        history_ids = self.client.zrevrange('ai_history:timeline', 0, limit - 1)
        history = []
        for history_id in history_ids:
            data = self.client.hgetall(history_id)
            if data:
                for key in ['analysis', 'logs_analyzed']:
                    if key in data:
                        try:
                            data[key] = json.loads(data[key])
                        except:
                            pass
                history.append(data)
        return history
    
    def get_analysis_history_entry(self, history_id: str) -> Optional[Dict]:
        """Get a single AI analysis history entry"""
        data = self.client.hgetall(history_id)
        if not data:
            return None
        for key in ['analysis', 'logs_analyzed']:
            if key in data:
                try:
                    data[key] = json.loads(data[key])
                except:
                    pass
        return data
    
    def delete_analysis_history(self, history_id: str) -> bool:
        """Delete an AI analysis history entry"""
        if not self.client.exists(history_id):
            return False
        self.client.delete(history_id)
        self.client.zrem('ai_history:timeline', history_id)
        return True
    
    def clear_all_analysis_history(self) -> int:
        """Clear all AI analysis history entries"""
        history_ids = self.client.zrange('ai_history:timeline', 0, -1)
        count = 0
        for history_id in history_ids:
            self.client.delete(history_id)
            count += 1
        self.client.delete('ai_history:timeline')
        return count
    
    # ==================== STATS OPERATIONS ====================
    
    def get_stats(self) -> Dict:
        """Get system statistics"""
        now = time.time()
        hour_ago = now - 3600
        day_ago = now - 86400
        
        return {
            'total_logs': self.get_logs_count(),
            'logs_last_hour': self.client.zcount('logs:timeline', hour_ago, now),
            'logs_last_day': self.client.zcount('logs:timeline', day_ago, now),
            'total_filters': self.client.scard('filters:all'),
            'total_alerts': self.client.zcard('alerts:timeline'),
            'unacknowledged_alerts': len(self.get_alerts(100, acknowledged=False)),
            'sources': self.get_sources(),
            'severities': self.get_severities()
        }
    
    # ==================== USER OPERATIONS ====================
    
    def create_user(self, user_data: Dict) -> str:
        """Create a new user"""
        user_id = f"user:{int(time.time() * 1000)}"
        user_data['id'] = user_id
        user_data['created_at'] = datetime.now(timezone.utc).isoformat()
        
        self.client.hset(user_id, mapping={k: json.dumps(v) if isinstance(v, (dict, list, bool)) else str(v) for k, v in user_data.items()})
        self.client.sadd('users:all', user_id)
        
        # Index by username for quick lookup
        self.client.set(f"users:username:{user_data['username'].lower()}", user_id)
        
        return user_id
    
    def get_user(self, user_id: str) -> Optional[Dict]:
        """Get a user by ID"""
        data = self.client.hgetall(user_id)
        if not data:
            return None
        return data
    
    def get_user_by_username(self, username: str) -> Optional[Dict]:
        """Get a user by username"""
        user_id = self.client.get(f"users:username:{username.lower()}")
        if not user_id:
            return None
        return self.get_user(user_id)
    
    def get_all_users(self) -> List[Dict]:
        """Get all users"""
        user_ids = self.client.smembers('users:all')
        users = []
        for user_id in user_ids:
            user = self.get_user(user_id)
            if user:
                users.append(user)
        return sorted(users, key=lambda x: x.get('username', ''))
    
    def update_user(self, user_id: str, user_data: Dict) -> bool:
        """Update a user"""
        if not self.client.exists(user_id):
            return False
        self.client.hset(user_id, mapping={k: json.dumps(v) if isinstance(v, (dict, list, bool)) else str(v) for k, v in user_data.items()})
        return True
    
    def delete_user(self, user_id: str) -> bool:
        """Delete a user"""
        user = self.get_user(user_id)
        if not user:
            return False
        
        # Remove username index
        if user.get('username'):
            self.client.delete(f"users:username:{user['username'].lower()}")
        
        self.client.delete(user_id)
        self.client.srem('users:all', user_id)
        return True
    
    def ensure_admin_exists(self):
        """Ensure at least one admin user exists"""
        from werkzeug.security import generate_password_hash
        
        users = self.get_all_users()
        admin_exists = any(u.get('role') == 'admin' for u in users)
        
        if not admin_exists:
            # Create default admin user
            admin_data = {
                'username': 'admin',
                'password_hash': generate_password_hash('admin'),
                'email': '',
                'role': 'admin'
            }
            self.create_user(admin_data)
            print("[Redis] Created default admin user (username: admin, password: admin)")
            return True
        return False


# Global instance
redis_client = RedisClient()
