# Copyright (C) 2026 Fotios Tsiadimos
# SPDX-License-Identifier: GPL-3.0-or-later

import ollama
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional, Callable
from config import Config

class OllamaAnalyzer:
    """AI-powered log analyzer using Ollama"""
    
    def __init__(self, alert_callback: Callable = None, cache_ttl: int = 30):
        self.host = Config.OLLAMA_HOST
        self.model = Config.OLLAMA_MODEL
        self.alert_callback = alert_callback
        self.running = False
        self.analysis_thread = None

        # Caching for availability and models to reduce frequent /api/tags calls
        self._cache_ttl = cache_ttl  # seconds
        self._last_check_time = 0.0
        self._last_available = False
        self._last_models: List[str] = []
        # Lock to prevent concurrent refreshes (de-duplicate simultaneous checks)
        self._check_lock = threading.Lock()
        
    def is_available(self, force_refresh: bool = False) -> bool:
        """Check if Ollama is available. Uses short TTL cache and a lock to avoid frequent/duplicate client.list() calls."""
        now = time.time()
        if not force_refresh and (now - self._last_check_time) < self._cache_ttl:
            return self._last_available

        # Ensure only one thread performs the actual client.list() call at a time
        with self._check_lock:
            # Re-check cache after acquiring lock (another thread may have refreshed)
            now = time.time()
            if not force_refresh and (now - self._last_check_time) < self._cache_ttl:
                return self._last_available

            try:
                client = ollama.Client(host=self.host)
                response = client.list()
                models = [m['name'] for m in response.get('models', [])]
                self._last_available = True
                self._last_models = models
                self._last_check_time = time.time()
                return True
            except Exception as e:
                print(f"[OllamaAnalyzer] Ollama not available: {e}")
                self._last_available = False
                self._last_models = []
                self._last_check_time = time.time()
                return False
    
    def get_models(self, force_refresh: bool = False) -> List[str]:
        """Get available Ollama models, using cached result when recent. Delegates refresh to is_available to avoid duplicate list calls."""
        now = time.time()
        if not force_refresh and (now - self._last_check_time) < self._cache_ttl and self._last_models:
            return self._last_models

        # Use is_available which handles locking and refresh
        self.is_available(force_refresh=force_refresh)
        return self._last_models

    def cached_availability(self) -> bool:
        """Return the last known availability without triggering a refresh."""
        return self._last_available

    def last_check_age(self) -> Optional[float]:
        """Return seconds since last Ollama check, or None if never checked."""
        if self._last_check_time == 0.0:
            return None
        return time.time() - self._last_check_time
    
    def analyze_log(self, log_entry: Dict) -> Dict:
        """Analyze a single log entry"""
        try:
            client = ollama.Client(host=self.host)
            
            prompt = f"""Analyze this server log entry and provide a brief assessment:

Hostname/IP: {log_entry.get('hostname', log_entry.get('source', 'unknown'))}
Source: {log_entry.get('source', 'unknown')}
Severity: {log_entry.get('severity', 'unknown')}
Program: {log_entry.get('program', 'unknown')}
Message: {log_entry.get('message', '')}

Provide a JSON response with:
1. "is_critical": boolean - true if this requires immediate attention
2. "category": string - one of: security, performance, application, system, network, other
3. "summary": string - brief one-line summary
4. "recommendation": string - what action to take, if any
5. "alert_user": boolean - true if user should be notified via Telegram

Respond ONLY with valid JSON, no other text."""

            response = client.generate(
                model=self.model,
                prompt=prompt,
                options={
                    'temperature': 0.3,
                    'num_predict': 256
                }
            )
            
            # Parse the response
            import json
            import re
            
            response_text = response['response'].strip()
            analysis = None
            
            # Try direct JSON parse first
            try:
                analysis = json.loads(response_text)
            except json.JSONDecodeError:
                pass
            
            # Try to extract JSON from markdown code blocks
            if analysis is None:
                code_block_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response_text, re.DOTALL)
                if code_block_match:
                    try:
                        analysis = json.loads(code_block_match.group(1))
                    except json.JSONDecodeError:
                        pass
            
            # Try to find JSON object with balanced braces
            if analysis is None:
                start_idx = response_text.find('{')
                if start_idx != -1:
                    brace_count = 0
                    end_idx = start_idx
                    for i, char in enumerate(response_text[start_idx:], start_idx):
                        if char == '{':
                            brace_count += 1
                        elif char == '}':
                            brace_count -= 1
                            if brace_count == 0:
                                end_idx = i + 1
                                break
                    try:
                        json_str = response_text[start_idx:end_idx]
                        analysis = json.loads(json_str)
                    except json.JSONDecodeError:
                        pass
            
            # Fallback if nothing worked
            if analysis is None:
                analysis = {
                    'is_critical': False,
                    'category': 'other',
                    'summary': response_text[:200] if response_text else 'Unable to parse response',
                    'recommendation': '',
                    'alert_user': False
                }
            
            return {
                'success': True,
                'analysis': analysis
            }
            
        except Exception as e:
            print(f"[OllamaAnalyzer] Error analyzing log: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def analyze_logs_batch(self, logs: List[Dict]) -> Dict:
        """Analyze multiple logs and summarize findings"""
        try:
            client = ollama.Client(host=self.host)
            
            # Prepare log summary for analysis (include hostname/IP for each log)
            log_summary = "\n".join([
                f"[{l.get('severity', 'info').upper()}] [{l.get('hostname', l.get('source', 'unknown'))}] {l.get('program', 'unknown')}: {l.get('message', '')[:200]}"
                for l in logs[:20]  # Limit to 20 logs
            ])
            
            prompt = f"""Analyze these server logs and provide a security/health assessment.
Each log line format: [SEVERITY] [HOSTNAME/IP] PROGRAM: MESSAGE

LOGS:
{log_summary}

Provide a JSON response with:
1. "overall_status": string - one of: healthy, warning, critical
2. "issues_found": array of strings - list of issues, each MUST start with [HOSTNAME/IP] e.g. "[192.168.1.10] SSH authentication failed"
3. "critical_count": number - count of critical issues
4. "recommendations": array of strings - actions to take, each MUST start with [HOSTNAME/IP] e.g. "[server1.local] Check SSH configuration"
5. "affected_hosts": array of strings - list of all hostnames/IPs that have issues
6. "alert_message": string - message for admin if critical issues (empty if none)

CRITICAL: Every issue and recommendation MUST begin with the hostname/IP in brackets so the admin knows which server to fix.

Respond ONLY with valid JSON, no other text."""

            response = client.generate(
                model=self.model,
                prompt=prompt,
                options={
                    'temperature': 0.3,
                    'num_predict': 512
                }
            )
            
            # Parse the response
            import json
            import re
            
            response_text = response['response'].strip()
            analysis = None
            
            # Try direct JSON parse first
            try:
                analysis = json.loads(response_text)
            except json.JSONDecodeError:
                pass
            
            # Try to extract JSON from markdown code blocks
            if analysis is None:
                code_block_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response_text, re.DOTALL)
                if code_block_match:
                    try:
                        analysis = json.loads(code_block_match.group(1))
                    except json.JSONDecodeError:
                        pass
            
            # Try to find JSON object with balanced braces
            if analysis is None:
                start_idx = response_text.find('{')
                if start_idx != -1:
                    brace_count = 0
                    end_idx = start_idx
                    for i, char in enumerate(response_text[start_idx:], start_idx):
                        if char == '{':
                            brace_count += 1
                        elif char == '}':
                            brace_count -= 1
                            if brace_count == 0:
                                end_idx = i + 1
                                break
                    try:
                        json_str = response_text[start_idx:end_idx]
                        analysis = json.loads(json_str)
                    except json.JSONDecodeError:
                        pass
            
            # Fallback if nothing worked
            if analysis is None:
                analysis = {
                    'overall_status': 'unknown',
                    'issues_found': ['Unable to parse AI response'],
                    'critical_count': 0,
                    'recommendations': [],
                    'affected_hosts': [],
                    'alert_message': ''
                }
            
            return {
                'success': True,
                'analysis': analysis,
                'logs_analyzed': len(logs)
            }
            
        except Exception as e:
            print(f"[OllamaAnalyzer] Error in batch analysis: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def generate_alert_message(self, log_entry: Dict, analysis: Dict) -> str:
        """Generate a human-readable alert message"""
        try:
            client = ollama.Client(host=self.host)
            
            prompt = f"""Generate a concise Telegram alert message for this log event:

Source: {log_entry.get('source', 'unknown')}
Severity: {log_entry.get('severity', 'unknown')}
Message: {log_entry.get('message', '')}
Analysis: {analysis}

The message should be:
- Clear and actionable
- Under 200 characters
- Include emoji for visual indication
- Start with severity indicator

Respond with ONLY the message text, nothing else."""

            response = client.generate(
                model=self.model,
                prompt=prompt,
                options={
                    'temperature': 0.5,
                    'num_predict': 100
                }
            )
            
            return response['response'].strip()
            
        except Exception as e:
            # Fallback message
            severity_emoji = {
                'critical': '🔴',
                'error': '🟠',
                'warning': '🟡',
                'info': '🔵'
            }.get(log_entry.get('severity', 'info'), '⚪')
            
            hostname = log_entry.get('hostname', log_entry.get('source', 'unknown'))
            return f"{severity_emoji} [{log_entry.get('severity', 'info').upper()}] [{hostname}] {log_entry.get('program', 'unknown')}: {log_entry.get('message', '')[:100]}"
    
    def chat(self, message: str, context: str = "") -> str:
        """Chat with the AI about logs"""
        try:
            client = ollama.Client(host=self.host)
            
            system_prompt = """You are LogAI Monitor, an expert system administrator assistant.

IMPORTANT RULES:
1. NEVER copy-paste raw log entries in your response
2. Always provide SUMMARIZED insights - brief, clear, and actionable
3. Keep responses SHORT (2-4 sentences max for simple questions)
4. Use bullet points for multiple findings
5. Focus on: What happened? Is it critical? What to do?
6. If logs look normal, say so briefly
7. Highlight only genuinely concerning patterns

CONTEXT UNDERSTANDING - Be smart about what the user is asking:
- If user asks about "alerts", "notifications", "unacknowledged" → Focus on the ALERTS section
- If user asks about "errors", "issues", "problems", "logs", "warnings" → Focus on the LOGS section, analyze log patterns
- If user asks general questions like "anything I should know?" → Check BOTH logs and alerts, prioritize real issues
- If user asks about specific services/hosts/programs → Search the logs for those specific items
- DON'T report alerts when user is clearly asking about log analysis
- DON'T report log errors when user is specifically asking about alerts

Response style examples:
- "All systems normal. No errors or warnings detected in recent logs."
- "⚠️ Found 3 failed SSH login attempts from IP 192.168.1.x. Consider blocking this IP."
- "🔴 Critical: Database connection errors detected. Check MySQL service status."
- "📋 You have 2 unacknowledged alerts that need attention."
- "No alerts currently. Your log activity shows normal operation."
"""

            if context:
                system_prompt += f"\n\nAvailable context (use ONLY what's relevant to the question):\n{context}"
            
            response = client.generate(
                model=self.model,
                prompt=f"{system_prompt}\n\nUser question: {message}\n\nProvide a brief, relevant answer based on what the user is actually asking about:",
                options={
                    'temperature': 0.5,
                    'num_predict': 400
                }
            )
            
            return response['response'].strip()
            
        except Exception as e:
            return f"Error communicating with AI: {str(e)}"
    
    def check_log_against_filters(self, log_entry: Dict, filters: List[Dict]) -> List[Dict]:
        """Check if a log matches any filters and should trigger an alert"""
        matched_filters = []
        
        for f in filters:
            if not f.get('enabled', True):
                continue
            
            conditions = f.get('conditions', {})
            matches = True
            
            # Check each condition
            if conditions.get('severity'):
                if log_entry.get('severity') not in conditions['severity']:
                    matches = False
            
            if conditions.get('source_contains') and matches:
                if conditions['source_contains'].lower() not in log_entry.get('source', '').lower():
                    matches = False
            
            if conditions.get('message_contains') and matches:
                if conditions['message_contains'].lower() not in log_entry.get('message', '').lower():
                    matches = False
            
            if conditions.get('message_regex') and matches:
                import re
                if not re.search(conditions['message_regex'], log_entry.get('message', ''), re.IGNORECASE):
                    matches = False
            
            if matches:
                matched_filters.append(f)
        
        return matched_filters
