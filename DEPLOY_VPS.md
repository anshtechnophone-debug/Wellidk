# XYRON VPS deployment (low-budget)

This guide runs one XYRON polling process on an Ubuntu/Debian Linux VPS. Aim for at least 1 GB RAM; 2 GB is more comfortable. A ₹150/month price is provider-dependent, so confirm checkout price, taxes, renewal pricing, and resources before paying.

## 1. Install requirements

SSH into your server, then run:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip
```

XYRON requires Python 3.12+. Check with `python3 --version`; if older, install Python 3.12 from a trusted source before continuing.

## 2. Clone and install

```bash
sudo adduser --system --group --home /opt/xyron xyron
sudo -u xyron git clone https://github.com/anshtechnophone-debug/Wellidk.git /opt/xyron
sudo -u xyron python3 -m venv /opt/xyron/.venv
sudo -u xyron /opt/xyron/.venv/bin/python -m pip install --upgrade pip
sudo -u xyron /opt/xyron/.venv/bin/python -m pip install /opt/xyron
sudo mkdir -p /opt/xyron/data
sudo chown -R xyron:xyron /opt/xyron
```

## 3. Add private configuration

Create the environment file outside the repository:

```bash
sudo mkdir -p /etc/xyron
sudo install -o root -g xyron -m 640 /dev/null /etc/xyron/xyron.env
sudo nano /etc/xyron/xyron.env
```

Add the required settings (replace placeholders locally; never paste secrets into public chats):

```dotenv
BOT_TOKEN=YOUR_NEW_BOTFATHER_TOKEN
OWNER_ID=YOUR_NUMERIC_TELEGRAM_USER_ID
OWNER_ADMIN_REQUIRED=true
DATABASE_URL=sqlite+aiosqlite:////opt/xyron/data/xyron.db
LOG_LEVEL=INFO
TIMEZONE=Asia/Kolkata
AI_ENABLED=false
AI_GLOBAL_EMERGENCY_STOP=true
```

If the bot token has been exposed, revoke it via @BotFather and use a newly generated token. Keep AI disabled unless you intentionally configure a provider, model, API key, and budget.

## 4. Migrate and start

```bash
cd /opt/xyron
sudo -u xyron env DATABASE_URL='sqlite+aiosqlite:////opt/xyron/data/xyron.db' /opt/xyron/.venv/bin/alembic upgrade head
sudo cp /opt/xyron/deploy/xyron.service /etc/systemd/system/xyron.service
sudo systemctl daemon-reload
sudo systemctl enable --now xyron
sudo systemctl status xyron --no-pager
```

Read logs with `sudo journalctl -u xyron -f`. The service uses systemd to restart after a crash and start after reboot; this cannot prevent provider outages or resource exhaustion.

## 5. Updates and backups

Stop XYRON before updating, so there is never more than one poller for this bot token:

```bash
sudo systemctl stop xyron
cd /opt/xyron
sudo -u xyron git pull --ff-only
sudo -u xyron /opt/xyron/.venv/bin/python -m pip install /opt/xyron
sudo -u xyron env DATABASE_URL='sqlite+aiosqlite:////opt/xyron/data/xyron.db' /opt/xyron/.venv/bin/alembic upgrade head
sudo systemctl start xyron
sudo journalctl -u xyron -n 100 --no-pager
```

Before an update, make a private database backup: `sudo cp /opt/xyron/data/xyron.db /opt/xyron/data/xyron.db.backup && sudo chown xyron:xyron /opt/xyron/data/xyron.db.backup && sudo chmod 600 /opt/xyron/data/xyron.db.backup`.

Only run one XYRON polling process against this token/database. Confirm provider costs and resource limits before purchase; this guide does not provision the server.
