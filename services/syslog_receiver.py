# Copyright (C) 2026 Fotios Tsiadimos
# SPDX-License-Identifier: GPL-3.0-or-later

import socket
import threading
import re
import time
import os
import signal
from datetime import datetime, timezone
from typing import Callable, Optional, Dict, List
from config import Config

class SyslogReceiver:
    """UDP/TCP Syslog receiver for collecting logs from rsyslog"""
    
    # Syslog severity levels
    SEVERITIES = {
        0: 'emergency',
        1: 'alert',
        2: 'critical',
        3: 'error',
        4: 'warning',
        5: 'notice',
        6: 'info',
        7: 'debug'
    }
    
    # Syslog facilities
    FACILITIES = {
        0: 'kern',
        1: 'user',
        2: 'mail',
        3: 'daemon',
        4: 'auth',
        5: 'syslog',
        6: 'lpr',
        7: 'news',
        8: 'uucp',
        9: 'cron',
        10: 'authpriv',
        11: 'ftp',
        16: 'local0',
        17: 'local1',
        18: 'local2',
        19: 'local3',
        20: 'local4',
        21: 'local5',
        22: 'local6',
        23: 'local7'
    }
    
    def __init__(self, callback: Callable = None):
        self.host = Config.SYSLOG_HOST
        self.port = Config.SYSLOG_PORT
        self.callback = callback
        self.running = False
        self.udp_socket = None
        self.tcp_socket = None
        self.threads = []
        
        # Client tracking for diagnostics
        self._client_stats: Dict[str, Dict] = {}  # {ip: {stats}}
        self._client_stats_lock = threading.Lock()
        self._start_time = None
        
        # Track port binding status separately from socket objects
        self._udp_bound = False
        self._tcp_bound = False
        
        # Track if we're the primary receiver (for Flask debug mode handling)
        self._is_primary = False
    
    @staticmethod
    def _check_port_in_use(port: int, protocol: str = 'udp') -> List[Dict]:
        """
        Check if a port is already in use and return info about the processes using it.
        Works on Linux (including containers) by parsing /proc/net/udp and /proc/net/tcp.
        Returns list of dicts with 'pid', 'name', 'port' info.
        """
        processes = []
        
        # Method 1: Try using socket test (quick check)
        try:
            sock_type = socket.SOCK_DGRAM if protocol == 'udp' else socket.SOCK_STREAM
            test_sock = socket.socket(socket.AF_INET, sock_type)
            test_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            test_sock.bind(('0.0.0.0', port))
            test_sock.close()
            return []  # Port is free
        except OSError:
            pass  # Port is in use, try to find who
        
        # Method 2: Parse /proc/net files (works in containers)
        net_file = f'/proc/net/{protocol}'
        if os.path.exists(net_file):
            try:
                with open(net_file, 'r') as f:
                    lines = f.readlines()[1:]  # Skip header
                
                for line in lines:
                    parts = line.split()
                    if len(parts) >= 10:
                        # local_address is in hex format: IP:PORT
                        local_addr = parts[1]
                        hex_port = local_addr.split(':')[1]
                        local_port = int(hex_port, 16)
                        
                        if local_port == port:
                            inode = parts[9]
                            pid, name = SyslogReceiver._find_process_by_inode(inode)
                            if pid:
                                processes.append({
                                    'pid': pid,
                                    'name': name,
                                    'port': port,
                                    'protocol': protocol
                                })
            except Exception as e:
                print(f"[SyslogReceiver] Warning: Could not parse {net_file}: {e}")
        
        # Method 3: Try lsof as fallback (may not be available in containers)
        if not processes:
            try:
                import subprocess
                result = subprocess.run(
                    ['lsof', '-i', f'{protocol.upper()}:{port}', '-t'],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0 and result.stdout.strip():
                    for pid_str in result.stdout.strip().split('\n'):
                        try:
                            pid = int(pid_str)
                            name = SyslogReceiver._get_process_name(pid)
                            processes.append({
                                'pid': pid,
                                'name': name,
                                'port': port,
                                'protocol': protocol
                            })
                        except ValueError:
                            pass
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass  # lsof not available
        
        return processes
    
    @staticmethod
    def _find_process_by_inode(inode: str) -> tuple:
        """Find process PID and name by socket inode"""
        try:
            for pid in os.listdir('/proc'):
                if not pid.isdigit():
                    continue
                fd_path = f'/proc/{pid}/fd'
                try:
                    for fd in os.listdir(fd_path):
                        try:
                            link = os.readlink(f'{fd_path}/{fd}')
                            if f'socket:[{inode}]' in link:
                                name = SyslogReceiver._get_process_name(int(pid))
                                return int(pid), name
                        except (OSError, PermissionError):
                            continue
                except (OSError, PermissionError):
                    continue
        except Exception:
            pass
        return None, None
    
    @staticmethod
    def _get_process_name(pid: int) -> str:
        """Get process name from PID"""
        try:
            with open(f'/proc/{pid}/comm', 'r') as f:
                return f.read().strip()
        except:
            return 'unknown'
    
    @staticmethod
    def _is_same_process(pid: int) -> bool:
        """Check if the given PID is the current process or its parent"""
        current_pid = os.getpid()
        parent_pid = os.getppid()
        return pid == current_pid or pid == parent_pid
    
    def _ensure_port_available(self, port: int, protocol: str = 'udp') -> bool:
        """
        Ensure the port is available. If another process is using it, 
        warn and optionally kill it if it's a stale Flask process.
        Returns True if port is now available, False otherwise.
        """
        processes = self._check_port_in_use(port, protocol)
        
        if not processes:
            return True
        
        current_pid = os.getpid()
        
        for proc in processes:
            pid = proc['pid']
            name = proc['name']
            
            # Skip if it's ourselves
            if self._is_same_process(pid):
                print(f"[SyslogReceiver] Warning: {protocol.upper()} port {port} already bound by current process tree (PID {pid})")
                return False  # Already bound by us, don't try again
            
            print(f"[SyslogReceiver] Warning: {protocol.upper()} port {port} is in use by process {name} (PID {pid})")
            
            # Check if it's a Flask/Python process that might be stale
            if name in ('python', 'python3', 'flask'):
                # Check if it's a zombie or stopped process
                try:
                    with open(f'/proc/{pid}/status', 'r') as f:
                        status_content = f.read()
                        if 'State:\tZ' in status_content or 'State:\tT' in status_content:
                            print(f"[SyslogReceiver] Process {pid} appears to be zombie/stopped. Attempting to terminate...")
                            try:
                                os.kill(pid, signal.SIGTERM)
                                time.sleep(0.5)
                                # Check if still exists
                                if os.path.exists(f'/proc/{pid}'):
                                    os.kill(pid, signal.SIGKILL)
                                    time.sleep(0.5)
                                print(f"[SyslogReceiver] Successfully terminated stale process {pid}")
                            except (ProcessLookupError, PermissionError) as e:
                                print(f"[SyslogReceiver] Could not terminate process {pid}: {e}")
                except (FileNotFoundError, PermissionError):
                    pass
        
        # Re-check if port is now available
        time.sleep(0.2)
        return len(self._check_port_in_use(port, protocol)) == 0
    
    def _track_client(self, source_ip: str, protocol: str, success: bool = True, error_msg: str = None, hostname: str = None):
        """Track client statistics for diagnostics"""
        with self._client_stats_lock:
            now = time.time()
            if source_ip not in self._client_stats:
                self._client_stats[source_ip] = {
                    'ip': source_ip,
                    'hostname': hostname or source_ip,
                    'first_seen': now,
                    'last_seen': now,
                    'protocols': set(),
                    'message_count': 0,
                    'error_count': 0,
                    'last_error': None,
                    'last_error_time': None,
                    'messages_last_minute': [],  # timestamps
                    'messages_last_hour': 0,
                }
            
            stats = self._client_stats[source_ip]
            stats['last_seen'] = now
            stats['protocols'].add(protocol)
            if hostname and hostname != source_ip:
                stats['hostname'] = hostname
            
            if success:
                stats['message_count'] += 1
                # Track messages in the last minute (for rate calculation)
                stats['messages_last_minute'].append(now)
                # Clean old entries (older than 1 minute)
                stats['messages_last_minute'] = [t for t in stats['messages_last_minute'] if now - t < 60]
            else:
                stats['error_count'] += 1
                stats['last_error'] = error_msg
                stats['last_error_time'] = now
    
    def get_client_diagnostics(self) -> Dict:
        """Get diagnostic information about all clients"""
        with self._client_stats_lock:
            now = time.time()
            clients = []
            
            for ip, stats in self._client_stats.items():
                # Calculate messages per minute
                recent_messages = [t for t in stats['messages_last_minute'] if now - t < 60]
                msgs_per_minute = len(recent_messages)
                
                # Determine status
                seconds_since_last = now - stats['last_seen']
                if seconds_since_last < 60:
                    status = 'active'
                elif seconds_since_last < 300:
                    status = 'idle'
                else:
                    status = 'stale'
                
                # Check for issues
                issues = []
                if stats['error_count'] > 0:
                    issues.append(f"Errors: {stats['error_count']}")
                if stats['last_error']:
                    issues.append(f"Last error: {stats['last_error']}")
                if seconds_since_last > 600:
                    issues.append(f"No messages for {int(seconds_since_last / 60)} minutes")
                
                clients.append({
                    'ip': ip,
                    'hostname': stats['hostname'],
                    'protocols': list(stats['protocols']),
                    'first_seen': datetime.fromtimestamp(stats['first_seen'], timezone.utc).isoformat(),
                    'last_seen': datetime.fromtimestamp(stats['last_seen'], timezone.utc).isoformat(),
                    'seconds_since_last': int(seconds_since_last),
                    'message_count': stats['message_count'],
                    'messages_per_minute': msgs_per_minute,
                    'error_count': stats['error_count'],
                    'last_error': stats['last_error'],
                    'last_error_time': datetime.fromtimestamp(stats['last_error_time'], timezone.utc).isoformat() if stats['last_error_time'] else None,
                    'status': status,
                    'issues': issues
                })
            
            # Sort by last_seen (most recent first)
            clients.sort(key=lambda x: x['last_seen'], reverse=True)
            
            return {
                'receiver_running': self.running,
                'uptime_seconds': int(now - self._start_time) if self._start_time else 0,
                'udp_port': self.port,
                'tcp_port': self.port + 1,
                'udp_bound': self._udp_bound,
                'tcp_bound': self._tcp_bound,
                'is_primary': self._is_primary,
                'total_clients': len(clients),
                'active_clients': len([c for c in clients if c['status'] == 'active']),
                'clients_with_issues': len([c for c in clients if c['issues']]),
                'clients': clients
            }
        
    def parse_syslog_message(self, data: bytes, source_ip: str) -> dict:
        """Parse a syslog message (RFC 3164 and RFC 5424)"""
        try:
            message = data.decode('utf-8', errors='replace').strip()
        except:
            message = str(data)
        
        log_entry = {
            'source': source_ip,
            'source_type': 'syslog',
            'raw_message': message,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'facility': 'unknown',
            'severity': 'info',
            'hostname': source_ip,
            'program': 'unknown',
            'message': message
        }
        
        # Parse PRI (priority) field: <PRI>
        pri_match = re.match(r'^<(\d{1,3})>', message)
        if pri_match:
            pri = int(pri_match.group(1))
            facility = pri >> 3
            severity = pri & 0x07
            
            log_entry['facility'] = self.FACILITIES.get(facility, f'facility{facility}')
            log_entry['severity'] = self.SEVERITIES.get(severity, 'info')
            
            message = message[pri_match.end():]
        
        # Try to parse RFC 3164 format: Mmm dd hh:mm:ss hostname program[pid]: message
        rfc3164_match = re.match(
            r'^(\w{3}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})\s+(\S+)\s+(\S+?)(?:\[(\d+)\])?:\s*(.*)',
            message
        )
        
        if rfc3164_match:
            timestamp_str, hostname, program, pid, msg = rfc3164_match.groups()
            log_entry['hostname'] = hostname
            log_entry['program'] = program
            if pid:
                log_entry['pid'] = pid
            log_entry['message'] = msg
        else:
            # Try RFC 5424 format
            rfc5424_match = re.match(
                r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s*(.*)',
                message
            )
            
            if rfc5424_match:
                timestamp_str, hostname, app_name, proc_id, msg_id, structured_data, msg = rfc5424_match.groups()
                log_entry['timestamp'] = timestamp_str
                log_entry['hostname'] = hostname
                log_entry['program'] = app_name
                log_entry['proc_id'] = proc_id
                log_entry['msg_id'] = msg_id
                log_entry['message'] = msg if msg else structured_data
            else:
                # Fallback: use the whole message
                log_entry['message'] = message
        
        return log_entry
    
    def handle_udp(self):
        """Handle incoming UDP syslog messages"""
        self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Note: Removed SO_REUSEPORT because Flask debug mode creates two processes
        # and messages would be randomly distributed between them
        self.udp_socket.bind((self.host, self.port))
        self.udp_socket.settimeout(1.0)
        self._udp_bound = True
        
        print(f"[SyslogReceiver] UDP listening on {self.host}:{self.port}")
        
        while self.running:
            try:
                data, addr = self.udp_socket.recvfrom(65535)
                if data:
                    print(f"[SyslogReceiver] UDP received {len(data)} bytes from {addr[0]}:{addr[1]}")
                    print(f"[SyslogReceiver] Raw data: {data[:200]}")
                    log_entry = self.parse_syslog_message(data, addr[0])
                    print(f"[SyslogReceiver] Parsed: hostname={log_entry.get('hostname')}, severity={log_entry.get('severity')}, message={log_entry.get('message', '')[:100]}")
                    # Track this client
                    self._track_client(
                        source_ip=addr[0],
                        protocol='UDP',
                        success=True,
                        hostname=log_entry.get('hostname')
                    )
                    if self.callback:
                        print(f"[SyslogReceiver] Calling callback...")
                        self.callback(log_entry)
                        print(f"[SyslogReceiver] Callback completed")
                    else:
                        print(f"[SyslogReceiver] WARNING: No callback set!")
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    print(f"[SyslogReceiver] UDP error: {e}")
                    import traceback
                    traceback.print_exc()
                    # Track error if we have an address
                    try:
                        if 'addr' in dir() and addr:
                            self._track_client(addr[0], 'UDP', success=False, error_msg=str(e))
                    except:
                        pass
    
    def handle_tcp_client(self, client_socket: socket.socket, addr):
        """Handle a TCP client connection"""
        client_ip = addr[0]
        try:
            buffer = b""
            while self.running:
                data = client_socket.recv(4096)
                if not data:
                    break
                
                buffer += data
                
                # Split by newline (syslog messages are typically newline-terminated)
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    if line:
                        log_entry = self.parse_syslog_message(line, client_ip)
                        # Track this client
                        self._track_client(
                            source_ip=client_ip,
                            protocol='TCP',
                            success=True,
                            hostname=log_entry.get('hostname')
                        )
                        if self.callback:
                            self.callback(log_entry)
        except Exception as e:
            if self.running:
                print(f"[SyslogReceiver] TCP client error: {e}")
                self._track_client(client_ip, 'TCP', success=False, error_msg=str(e))
        finally:
            client_socket.close()
    
    def handle_tcp(self):
        """Handle incoming TCP syslog connections"""
        try:
            self.tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.tcp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # Note: Removed SO_REUSEPORT because Flask debug mode creates two processes
            self.tcp_socket.bind((self.host, self.port + 1))  # TCP on port + 1
            self.tcp_socket.listen(10)
            self.tcp_socket.settimeout(1.0)
            self._tcp_bound = True
            
            print(f"[SyslogReceiver] TCP listening on {self.host}:{self.port + 1}")
        except OSError as e:
            print(f"[SyslogReceiver] TCP bind failed on port {self.port + 1}: {e}")
            print("[SyslogReceiver] TCP syslog will be unavailable, UDP still active")
            return
        
        while self.running:
            try:
                client_socket, addr = self.tcp_socket.accept()
                client_thread = threading.Thread(
                    target=self.handle_tcp_client,
                    args=(client_socket, addr),
                    daemon=True
                )
                client_thread.start()
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    print(f"[SyslogReceiver] TCP accept error: {e}")
    
    def start(self):
        """Start the syslog receiver"""
        if self.running:
            print("[SyslogReceiver] Already running")
            return
        
        # Check for Flask debug mode with reloader (creates two processes)
        is_werkzeug_reloader = os.environ.get('WERKZEUG_RUN_MAIN') == 'true'
        is_reloader_process = os.environ.get('WERKZEUG_RUN_MAIN') is None and os.environ.get('FLASK_DEBUG') == '1'
        
        if is_reloader_process:
            print("[SyslogReceiver] Detected Flask reloader parent process - skipping syslog receiver")
            print("[SyslogReceiver] The actual receiver will start in the child process")
            return
        
        # Check if UDP port is available
        print(f"[SyslogReceiver] Checking if UDP port {self.port} is available...")
        if not self._ensure_port_available(self.port, 'udp'):
            print(f"[SyslogReceiver] ERROR: UDP port {self.port} is not available!")
            print("[SyslogReceiver] Another process may be using this port.")
            print("[SyslogReceiver] If running Flask with debug/reload mode, try: flask run --no-reload")
            # Don't start - port conflict would cause issues
            return
        
        # Check if TCP port is available  
        print(f"[SyslogReceiver] Checking if TCP port {self.port + 1} is available...")
        tcp_available = self._ensure_port_available(self.port + 1, 'tcp')
        if not tcp_available:
            print(f"[SyslogReceiver] Warning: TCP port {self.port + 1} is not available, TCP syslog will be disabled")
        
        self.running = True
        self._start_time = time.time()
        self._is_primary = True
        
        # Start UDP handler
        udp_thread = threading.Thread(target=self.handle_udp, daemon=True)
        udp_thread.start()
        self.threads.append(udp_thread)
        
        # Start TCP handler only if port is available
        if tcp_available:
            tcp_thread = threading.Thread(target=self.handle_tcp, daemon=True)
            tcp_thread.start()
            self.threads.append(tcp_thread)
        
        print("[SyslogReceiver] Started")
    
    def stop(self):
        """Stop the syslog receiver"""
        self.running = False
        self._udp_bound = False
        self._tcp_bound = False
        
        if self.udp_socket:
            self.udp_socket.close()
        if self.tcp_socket:
            self.tcp_socket.close()
        
        for thread in self.threads:
            thread.join(timeout=2)
        
        print("[SyslogReceiver] Stopped")
