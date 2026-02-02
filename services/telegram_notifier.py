# Copyright (C) 2026 Fotios Tsiadimos
# SPDX-License-Identifier: GPL-3.0-or-later

import asyncio
import threading
from typing import Optional
from telegram import Bot
from telegram.error import TelegramError
from config import Config

class TelegramNotifier:
    """Telegram notification service"""
    
    def __init__(self):
        self.bot_token = Config.TELEGRAM_BOT_TOKEN
        self.chat_id = Config.TELEGRAM_CHAT_ID
        self.bot = None
        self.enabled = False
        self._loop = None
        self._thread = None
        
    def configure(self, bot_token: str, chat_id: str):
        """Configure the Telegram bot"""
        self.bot_token = bot_token
        self.chat_id = chat_id
        
        if bot_token and chat_id:
            try:
                self.bot = Bot(token=bot_token)
                self.enabled = True
                print("[TelegramNotifier] Configured successfully")
                return True
            except Exception as e:
                print(f"[TelegramNotifier] Configuration error: {e}")
                self.enabled = False
                return False
        else:
            self.enabled = False
            return False
    
    def _ensure_loop(self):
        """Ensure we have an event loop for async operations"""
        if self._loop is None or not self._loop.is_running():
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
            self._thread.start()
    
    def send_message(self, message: str, parse_mode: str = 'HTML') -> bool:
        """Send a message to the configured chat"""
        if not self.enabled or not self.bot:
            print("[TelegramNotifier] Not configured or disabled")
            return False
        
        try:
            self._ensure_loop()
            
            async def _send():
                await self.bot.send_message(
                    chat_id=self.chat_id,
                    text=message,
                    parse_mode=parse_mode
                )
            
            future = asyncio.run_coroutine_threadsafe(_send(), self._loop)
            future.result(timeout=10)
            
            print(f"[TelegramNotifier] Message sent successfully")
            return True
            
        except TelegramError as e:
            print(f"[TelegramNotifier] Telegram error: {e}")
            return False
        except Exception as e:
            print(f"[TelegramNotifier] Error sending message: {e}")
            return False
    
    def send_alert(self, severity: str, source: str, message: str, hostname: str = None, analysis: str = None) -> bool:
        """Send an alert notification"""
        severity_emoji = {
            'critical': '🔴',
            'emergency': '🔴',
            'alert': '🔴',
            'error': '🟠',
            'warning': '🟡',
            'notice': '🔵',
            'info': '⚪',
            'debug': '⚫'
        }.get(severity.lower(), '⚪')
        
        # Use hostname if provided, otherwise use source
        host_display = hostname or source
        
        alert_text = f"""
{severity_emoji} <b>LogAI Monitor Alert</b>

<b>Severity:</b> {severity.upper()}
<b>Host:</b> {host_display}
<b>Source:</b> {source}
<b>Message:</b>
<code>{message[:500]}</code>
"""
        
        if analysis:
            alert_text += f"\n<b>AI Analysis:</b>\n{analysis[:300]}"
        
        return self.send_message(alert_text)
    
    def send_summary(self, stats: dict, analysis: dict = None) -> bool:
        """Send a summary notification"""
        summary_text = f"""
📊 <b>LogAI Monitor Summary</b>

<b>Logs (24h):</b> {stats.get('logs_last_day', 0)}
<b>Logs (1h):</b> {stats.get('logs_last_hour', 0)}
<b>Active Alerts:</b> {stats.get('unacknowledged_alerts', 0)}
<b>Sources:</b> {len(stats.get('sources', []))}
"""
        
        if analysis:
            status_emoji = {
                'healthy': '✅',
                'warning': '⚠️',
                'critical': '🚨'
            }.get(analysis.get('overall_status', 'unknown'), '❓')
            
            summary_text += f"""
<b>Status:</b> {status_emoji} {analysis.get('overall_status', 'Unknown').upper()}
<b>Critical Issues:</b> {analysis.get('critical_count', 0)}
"""
            
            if analysis.get('issues_found'):
                summary_text += "\n<b>Issues:</b>\n"
                for issue in analysis['issues_found'][:5]:
                    summary_text += f"• {issue}\n"
            
            if analysis.get('affected_hosts'):
                summary_text += "\n<b>Affected Hosts:</b>\n"
                summary_text += ", ".join(analysis['affected_hosts'][:10])
                summary_text += "\n"
        
        return self.send_message(summary_text)
    
    def test_connection(self) -> tuple:
        """Test the Telegram connection"""
        if not self.bot_token or not self.chat_id:
            return False, "Bot token or chat ID not configured"
        
        try:
            self.configure(self.bot_token, self.chat_id)
            
            self._ensure_loop()
            
            async def _test():
                bot_info = await self.bot.get_me()
                return bot_info
            
            future = asyncio.run_coroutine_threadsafe(_test(), self._loop)
            bot_info = future.result(timeout=10)
            
            # Send test message
            test_result = self.send_message("🔔 <b>LogAI Monitor</b>\n\nTelegram connection test successful!")
            
            if test_result:
                return True, f"Connected as @{bot_info.username}"
            else:
                return False, "Failed to send test message"
            
        except Exception as e:
            return False, str(e)


# Global instance
telegram_notifier = TelegramNotifier()
