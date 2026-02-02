<h1 align="center">🛡️ LogRadarAI — LogAI Monitor</h1>

<p align="center">
  <strong>A powerful log monitoring and analysis application that collects logs from Linux servers (via rsyslog) and Docker containers, analyzes them using local AI (Ollama), and sends intelligent alerts via Telegram.</strong><br>
  Practical and easy to deploy and operate.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.9+-blue.svg" alt="Python 3.9+">
  <img src="https://img.shields.io/badge/flask-2.x-green.svg" alt="Flask 2.x">
  <img src="https://img.shields.io/badge/vue.js-3.x-brightgreen.svg" alt="Vue.js 3">
  <img src="https://img.shields.io/badge/license-GPL--3.0-orange.svg" alt="License GPL-3.0">
  <a href="https://hub.docker.com/r/ftsiadimos/logradaraiq"><img src="https://img.shields.io/docker/pulls/ftsiadimos/logradaraiq?style=flat-square&logo=docker" alt="Docker Pulls"></a>
</p>

---

## Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [Quick Start](#quick-start)
- [Docker Compose](#docker-compose)
- [Manual Installation](#manual-installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)

---

## 📸 Screenshots

| Dark Theme | Lite Theme |
| --- | --- |
| <a href="mis/image1.png" target="_blank"><img src="mis/image1.png" width="420" alt="Dashboard View"></a> | <a href="mis/image.png" target="_blank"><img src="mis/image.png" width="420" alt="AI Troubleshooter modal"></a> |
| *AI Analyzer* | *AI Analyzer* |

---

## Features

- 📊 **Dashboard** - Real-time overview of log statistics and system health
- 📝 **Log Collection** - Collect logs from rsyslog (UDP/TCP) and Docker containers
- 🤖 **AI Analysis** - Analyze logs using local Ollama AI for intelligent insights
- 🔔 **Smart Alerts** - Create filters to detect specific patterns and receive Telegram notifications
- 🐳 **Docker Integration** - Auto-discover and monitor Docker container logs
- 💬 **AI Chat** - Interactive chat assistant for log troubleshooting
- 🎨 **Modern UI** - Clean, responsive interface inspired by oVirt/Foreman

## Architecture

```
┌─────────────────┐      ┌─────────────────┐
│  Linux Servers  │────▶│   Syslog UDP    │
│   (rsyslog)     │      │   Port 5514     │
└─────────────────┘      └────────┬────────┘
                                  │
┌─────────────────┐      ┌────────▼────────┐      ┌─────────────────┐
│    Docker       │────▶│   LogAI         │────▶│     Redis       │
│   Containers    │      │   Monitor       │      │   (Storage)     │
└─────────────────┘      └────────┬────────┘      └─────────────────┘
                                  │
                         ┌────────▼────────┐      ┌─────────────────┐
                         │   Ollama AI     │────▶│    Telegram     │
                         │   (Analysis)    │      │   (Alerts)      │
                         └─────────────────┘      └─────────────────┘
```

## Quick Start

### Using Docker Compose (Recommended)

1. Clone the repository:
```bash
git clone https://github.com/yourusername/logaimonitor.git
cd logaimonitor
```

2. Start the application:
```bash
docker-compose up -d
```

Example `docker-compose` (use the included `docker-compose.example.yml` or drop it into your project root as `docker-compose.yml`):

```yaml
version: '3.8'
services:
  logaimonitor:
    image: ftsiadimos/logradaraiq
    container_name: logaimonitor
    restart: unless-stopped
    ports:
      - "5059:5059"      # Web UI
      - "5514:5514/udp"  # Syslog UDP
      - "5515:5515/tcp"  # Syslog TCP
    environment:
      - SECRET_KEY=${SECRET_KEY:-change-this-secret-key}
      - REDIS_HOST=redis
      - REDIS_PORT=6379
      - OLLAMA_HOST=${OLLAMA_HOST:-http://host.docker.internal:11434}
      - OLLAMA_MODEL=${OLLAMA_MODEL:-llama3.2}
      - TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN:-}
      - TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID:-}
      - LOG_RETENTION_HOURS=${LOG_RETENTION_HOURS:-168}
      - ANALYSIS_INTERVAL_SECONDS=${ANALYSIS_INTERVAL_SECONDS:-300}
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    depends_on:
      redis:
        condition: service_healthy
    networks:
      - logaimonitor-net
    extra_hosts:
      - "host.docker.internal:host-gateway"

  redis:
    image: redis:7-alpine
    container_name: logaimonitor-redis
    restart: unless-stopped
    volumes:
      - redis-data:/data
    command: redis-server --appendonly yes
    networks:
      - logaimonitor-net
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  # Optional: Ollama service (uncomment if you want to run Ollama in Docker)
  # ollama:
  #   image: ollama/ollama:latest
  #   container_name: logaimonitor-ollama
  #   restart: unless-stopped
  #   ports:
  #     - "11434:11434"
  #   volumes:
  #     - ollama-data:/root/.ollama
  #   networks:
  #     - logaimonitor-net
  #   # GPU support (requires nvidia-docker)
  #   # deploy:
  #   #   resources:
  #   #     reservations:
  #   #       devices:
  #   #         - driver: nvidia
  #   #           count: 1
  #   #           capabilities: [gpu]

volumes:
  redis-data:
  # ollama-data:

networks:
  logaimonitor-net:
    driver: bridge
```

3. Access the web interface at `http://localhost:5059`

### Manual Installation

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Start Redis:
```bash
docker run -d --name redis -p 6379:6379 redis:7-alpine
```

3. Install and start Ollama:
```bash
# Install Ollama
curl -fsSL https://ollama.ai/install.sh | sh

# Pull a model
ollama pull llama3.2

# Start Ollama server
ollama serve
```

4. Run the application:
```bash
python app.py
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `SECRET_KEY` | Flask secret key | `change-this` |
| `REDIS_HOST` | Redis hostname | `localhost` |
| `REDIS_PORT` | Redis port | `6379` |
| `OLLAMA_HOST` | Ollama API URL | `http://localhost:11434` |
| `OLLAMA_MODEL` | Ollama model name | `llama3.2` |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token | - |
| `TELEGRAM_CHAT_ID` | Telegram chat ID | - |
| `LOG_RETENTION_HOURS` | Log retention period | `168` (7 days) |
| `ANALYSIS_INTERVAL_SECONDS` | Auto-analysis interval | `300` |

### Configuring Rsyslog

On your Linux servers, add this configuration to `/etc/rsyslog.d/99-logaimonitor.conf`:

```bash
# Forward all logs via UDP
*.* @logaimonitor-host:5514

# Or via TCP (more reliable)
*.* @@logaimonitor-host:5515
```

Then restart rsyslog:
```bash
sudo systemctl restart rsyslog
```

#### Forward Specific Logs Only

If you only want to forward certain log types:

```bash
# Only auth/security logs
auth,authpriv.* @logaimonitor-host:5514

# Only errors and above
*.err @logaimonitor-host:5514

# Kernel messages
kern.* @logaimonitor-host:5514
```

#### Test with logger command

Send a test log immediately:
```bash
logger -n logaimonitor-host -P 5514 -d "Test message from server"
```

### Collecting Docker Logs from External Hosts

For Docker containers running on **external/remote hosts**, you have several options:

#### Option 1: Docker Syslog Logging Driver (Recommended)

On the **remote Docker host**, configure containers to send logs via syslog:

```bash
# Run containers with syslog driver
docker run -d \
  --log-driver=syslog \
  --log-opt syslog-address=udp://logaimonitor-host:5514 \
  --log-opt tag="{{.Name}}" \
  your-image
```

Or set as the default for all containers in `/etc/docker/daemon.json`:
```json
{
  "log-driver": "syslog",
  "log-opts": {
    "syslog-address": "udp://logaimonitor-host:5514",
    "tag": "{{.Name}}"
  }
}
```

Then restart Docker:
```bash
sudo systemctl restart docker
```

#### Option 2: Expose Docker Remote API

On the **remote host**, edit `/etc/docker/daemon.json`:
```json
{
  "hosts": ["unix:///var/run/docker.sock", "tcp://0.0.0.0:2375"]
}
```

Then on LogAI Monitor, set the environment variable:
```bash
DOCKER_SOCKET=tcp://remote-host:2375
```

⚠️ **Warning**: This exposes Docker without authentication. Use TLS certificates for production or restrict with firewall rules.

#### Option 3: Forward via rsyslog on Remote Host

Install rsyslog on the remote Docker host and configure journald forwarding:

```bash
# /etc/rsyslog.d/99-docker-forward.conf
module(load="imjournal")
:programname, startswith, "docker" @logaimonitor-host:5514
```

> **Recommendation**: Option 1 (syslog driver) is the easiest and most secure - no extra configuration on LogAI Monitor needed, logs appear as syslog entries.

### Setting up Telegram Notifications

1. Create a bot with [@BotFather](https://t.me/BotFather) on Telegram
2. Copy the bot token
3. Send a message to your bot
4. Get your chat ID from `https://api.telegram.org/bot<TOKEN>/getUpdates`
5. Configure in Settings or via environment variables

## Usage

### Creating Filters

Filters allow you to monitor specific log patterns:

1. Go to **Filters** in the sidebar
2. Click **Create Filter**
3. Configure conditions:
   - **Severity**: Match specific severity levels
   - **Source Contains**: Match logs from specific sources
   - **Message Contains**: Match logs containing specific text
   - **Message Regex**: Advanced pattern matching
4. Enable Telegram notification if desired
5. Save the filter

### AI Analysis

1. Go to **AI Analysis** in the sidebar
2. Click **Analyze Recent Logs** for batch analysis
3. Use the **Chat Assistant** to ask questions about your logs
4. Click on any log entry and use **Analyze with AI** for detailed analysis

### Viewing Docker Logs

1. Go to **Docker Containers** in the sidebar
2. View all running containers
3. Click **Logs** to view container logs
4. Logs are automatically collected and analyzed

## API Reference

### Logs

- `GET /api/logs` - Get logs with filtering
- `GET /api/logs/<id>` - Get single log
- `POST /api/logs/ingest` - Ingest log via HTTP

### Filters

- `GET /api/filters` - List all filters
- `POST /api/filters` - Create filter
- `PUT /api/filters/<id>` - Update filter
- `DELETE /api/filters/<id>` - Delete filter

### Alerts

- `GET /api/alerts` - List alerts
- `POST /api/alerts/<id>/acknowledge` - Acknowledge alert

### AI

- `GET /api/ollama/status` - Check Ollama status
- `POST /api/ollama/analyze` - Analyze logs
- `POST /api/ollama/chat` - Chat with AI

### Settings

- `GET /api/settings` - Get settings
- `POST /api/settings` - Save settings
- `POST /api/telegram/test` - Test Telegram connection

## Ports

| Port | Protocol | Description |
|------|----------|-------------|
| 5059 | TCP | Web interface |
| 5514 | UDP | Syslog (UDP) |
| 5515 | TCP | Syslog (TCP) |

## Tech Stack

- **Backend**: Python, Flask, Flask-SocketIO
- **Storage**: Redis
- **AI**: Ollama (local LLM)
- **Notifications**: Telegram Bot API
- **Frontend**: HTML, CSS, JavaScript
- **Deployment**: Docker, Docker Compose

## Troubleshooting

### Logs not appearing

1. Check rsyslog configuration on source servers
2. Verify network connectivity (ports 5514/5515)
3. Check firewall rules
4. View LogAI Monitor logs: `docker-compose logs -f logaimonitor`

### Ollama not working

1. Verify Ollama is running: `curl http://localhost:11434/api/tags`
2. Check the model is pulled: `ollama list`
3. Verify `OLLAMA_HOST` environment variable

### Telegram not sending messages

1. Verify bot token is correct
2. Check chat ID (must start a conversation with bot first)
3. Use "Test Connection" in Settings

## Contributing

Contributions are welcome! Please read our contributing guidelines and submit pull requests.

## License

GPL-3.0 License - see LICENSE file for details.

Copyright (C) 2026 Fotios Tsiadimos

## Acknowledgments

- [Ollama](https://ollama.ai) - Local AI inference
- [Flask](https://flask.palletsprojects.com/) - Web framework
- [Redis](https://redis.io/) - In-memory data store
- [Font Awesome](https://fontawesome.com/) - Icons
