# Copyright (C) 2026 Fotios Tsiadimos
# SPDX-License-Identifier: GPL-3.0-or-later

import os

class Config:
    """Application configuration"""
    
    # Flask
    SECRET_KEY = os.environ.get('SECRET_KEY', 'logaimonitor-secret-key-change-in-production')
    DEBUG = os.environ.get('DEBUG', 'True').lower() == 'true'
    
    # Redis
    REDIS_HOST = os.environ.get('REDIS_HOST', 'localhost')
    REDIS_PORT = int(os.environ.get('REDIS_PORT', 6379))
    REDIS_DB = int(os.environ.get('REDIS_DB', 0))
    REDIS_PASSWORD = os.environ.get('REDIS_PASSWORD', None)
    
    # Syslog receiver
    SYSLOG_HOST = os.environ.get('SYSLOG_HOST', '0.0.0.0')
    SYSLOG_PORT = int(os.environ.get('SYSLOG_PORT', 5514))
    
    # Docker
    DOCKER_SOCKET = os.environ.get('DOCKER_SOCKET', 'unix://var/run/docker.sock')
    
    # Ollama
    OLLAMA_HOST = os.environ.get('OLLAMA_HOST', 'http://localhost:11434')
    OLLAMA_MODEL = os.environ.get('OLLAMA_MODEL', 'llama3.2')
    # How long to cache Ollama availability/models (seconds)
    OLLAMA_CACHE_TTL_SECONDS = int(os.environ.get('OLLAMA_CACHE_TTL_SECONDS', 60))
    
    # Telegram
    TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')
    
    # Log retention (in hours)
    LOG_RETENTION_HOURS = int(os.environ.get('LOG_RETENTION_HOURS', 12))  # 12 hours
    
    # Analysis
    ANALYSIS_INTERVAL_SECONDS = int(os.environ.get('ANALYSIS_INTERVAL_SECONDS', 300))  # 5 minutes
    MAX_LOGS_PER_ANALYSIS = int(os.environ.get('MAX_LOGS_PER_ANALYSIS', 100))
