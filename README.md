# XYRON - One Bot. Every Feature.

XYRON is an asynchronous Telegram bot project. This initial release provides a working base for group administration: centralized live permission checks, member bans/mutes/kicks, warnings, moderation history, a database-backed expiration worker, basic utility commands, and SQLite/PostgreSQL persistence.

**This is not yet the full superbot described in the master build prompt.** The feature-status matrix below is authoritative. In particular, music playback, games, AI, anti-spam, RSS, reminders, welcome flows, and media processing are not implemented. Do not advertise those commands or treat this release as feature-complete.

## Requirements

- Python 3.12 (the package metadata currently requires 3.12 or newer).
- A Telegram bot created with BotFather and added to the target group.
- The bot must be promoted to administrator. Grant `Ban users` for moderation, and `Delete messages` for `d*` and `s*` variants. Pinning is not implemented.
- Set the numeric user ID of the primary owner in `OWNER_ID`. The username `@s1dh7` is not used for authorization.
- SQLite is sufficient for a single-process small deployment. PostgreSQL is supported for a larger deployment; only run one XYRON poller against a given bot token/database because the temporary-action worker is not a distributed job-claim system.

## Install And Run

```bash
git clone <repository-url> xyron
cd xyron
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
cp .env.example .env
```

Edit `.env` locally. Replace `BOT_TOKEN` and `OWNER_ID`; never commit `.env`, tokens, session strings, or database files. You can obtain your numeric Telegram ID from a trusted ID bot or Telegram client. Do not post secrets into a public chat.

Create/migrate the database and start polling:

```bash
set -a && source .env && set +a
alembic upgrade head
python -m app
```

The process creates the SQLite parent directory if needed, initializes missing development tables, sets Telegram's bot command list, and starts polling. Startup and shutdown are managed by `python-telegram-bot`; the expiration worker is stopped and the database engine disposed on shutdown.

For PostgreSQL, set a SQLAlchemy async URL, for example:

```text
DATABASE_URL=postgresql+asyncpg://xyron:change-this@127.0.0.1:5432/xyron
```

Provision the database and credentials separately. Keep PostgreSQL on a private network and use a dedicated least-privilege database role.

## Authorization Model

Administrative commands only work in groups/supergroups. On each execution, XYRON fetches fresh Telegram membership records for the configured owner, the actor, and the bot. By default, the configured owner must currently be a group administrator; if not, admin operations stop and XYRON attempts to notify the owner privately. The actor must independently be an administrator with the relevant Telegram permission, and the bot must be an administrator with the permission required for the action. Telegram's API remains authoritative and errors are returned rather than recorded as successful actions.

`OWNER_ADMIN_REQUIRED=true` is the default and recommended setting. Disabling it removes the owner-admin gate; it does not remove actor or bot permission checks. Protected IDs in `PROTECTED_USER_IDS` cannot be targeted. The group owner cannot be moderated. A group administrator cannot moderate another administrator; the group owner is the only actor permitted to do so by XYRON's hierarchy check.

`/kickme` uses only the invoking user's ID. It does not accept a target argument. It is available to regular members but requires the configured owner to remain an admin and XYRON to have `Ban users`.

Username resolution is deliberately limited. A username can only resolve after XYRON observed that user speaking in that group. Otherwise reply to the user's message or use their numeric ID. Usernames are mutable and Telegram does not provide a general username-to-ID lookup through the Bot API.

## Command Reference

All temporary durations accept `30s`, `4m`, `3h`, `6d`, or `5w`, with a maximum of 366 days. Reasons are optional. Reply-based target selection takes precedence over a target token.

| Command | Usage | Required access |
| --- | --- | --- |
| `/start`, `/help`, `/about`, `/ping` | Basic bot information | Anyone |
| `/id [reply]` | Show user and chat IDs | Anyone |
| `/modules` | Report current implementation status | Anyone |
| `/settings` | Inspect known groups, owner status, and current bot permissions | Configured owner only |
| `/ban <reply|id|known_username> [reason]` | Ban | Actor admin + bot `Ban users` + owner admin |
| `/dban ...` | Ban and attempt to delete the replied-to message | Same plus `Delete messages` |
| `/sban ...` | Ban and silently delete command | Same plus `Delete messages` |
| `/tban <reply|id|known_username> <duration> [reason]` | Durable temporary ban | Same as `/ban` |
| `/unban <reply|id|known_username>` | Unban and cancel pending temporary action | Actor admin + bot `Ban users` + owner admin |
| `/mute <reply|id|known_username> [reason]` | Restrict sending | Actor admin + bot `Ban users` + owner admin |
| `/dmute ...` | Mute and attempt to delete replied-to message | Same plus `Delete messages` |
| `/smute ...` | Mute and silently delete command | Same plus `Delete messages` |
| `/tmute <reply|id|known_username> <duration> [reason]` | Durable temporary restriction | Same as `/mute` |
| `/unmute <reply|id|known_username>` | Restore the group's default permissions | Actor admin + bot `Ban users` + owner admin |
| `/kick <reply|id|known_username> [reason]` | Remove via ban then unban | Actor admin + bot `Ban users` + owner admin |
| `/dkick ...` | Remove and attempt to delete replied-to message | Same plus `Delete messages` |
| `/skick ...` | Remove and silently delete command | Same plus `Delete messages` |
| `/kickme` | Remove the caller only | Any member; owner and bot gates apply |
| `/warn <reply|id|known_username> [reason]` | Record warning; default threshold applies a one-day mute | Actor admin + bot `Ban users` + owner admin |
| `/warns <reply|id|known_username>` | Count current warnings | Actor admin + owner admin |
| `/resetwarn <reply|id|known_username>` | Clear warnings | Actor admin + owner admin |
| `/setwarnlimit <1-20>` | Change group threshold | Actor admin + owner admin |
| `/modlog [1-25]` | Read recent moderation events | Actor admin + owner admin |
| `/adminlist` | Fetch current Telegram administrators | Actor admin + owner admin |
| `/report [reason]` | Reply to a message; send a report privately to admins who started the bot | Anyone |

Warn threshold action is currently a one-day mute. Although a `warn_action` database field exists, no command changes it; warning-limit ban selection is not exposed yet.

### Silent/Delete Semantics

`s*` means remove the command message after a successful operation. `d*` means delete the replied-to target message after the operation. Deletion failure is reported where a response remains possible. A successful moderation action is not misreported as failed if only message deletion failed. API failure does not create a success audit event.

## Data And Migrations

SQLAlchemy models cover groups, observed users, moderation events, temporary actions, warnings, and a reserved reminders table. IDs use 64-bit integer columns. Timestamps are validated as timezone-aware and normalized to UTC; SQLite timestamps are restored as UTC-aware values on reads.

Apply migrations with `alembic upgrade head`. Back up before upgrades. The initial downgrade drops application tables and therefore destroys their data; do not run `alembic downgrade base` on production unless you intentionally want that result. The current app startup creates missing tables for convenience but does not automatically evolve an existing schema; run Alembic for upgrades.

SQLite backup while the bot is stopped:

```bash
sqlite3 data/xyron.db ".backup 'data/xyron-$(date +%F).db'"
```

Restore with the bot stopped by replacing `data/xyron.db` with a verified backup. For PostgreSQL:

```bash
pg_dump --format=custom --file="xyron-$(date +%F).dump" "$DATABASE_URL"
pg_restore --clean --if-exists --dbname="$DATABASE_URL" xyron-YYYY-MM-DD.dump
```

Validate restore procedures in a staging database before relying on them.

## Tests And Checks

```bash
python -m pytest -q
ruff check app tests
python -m compileall -q app
```

Tests use mocked Telegram members and temporary SQLite databases. To integration-test, create a private group, add the bot and owner, grant only required rights, verify a non-admin cannot run admin commands, then test each success/failure path with a dedicated test account. Integration testing requires a real bot token and must be performed by the operator; no live Telegram deployment was performed for this release.

## Docker

```bash
cp .env.example .env
# Edit .env securely
docker compose build
mkdir -p data
sudo chown 10001:10001 data
docker compose run --rm xyron alembic upgrade head
docker compose up -d
docker compose logs -f xyron
```

The container runs as an unprivileged user and mounts `./data`. Keep the `.env` file permissions restrictive (`chmod 600 .env`). For PostgreSQL, configure a database service or managed endpoint and set `DATABASE_URL`; the included compose file intentionally does not expose a database port or ship a default database password.

## systemd

Create a service account and install the project under `/opt/xyron`. Put the environment file at `/etc/xyron/xyron.env` with mode `0600`, install `deploy/xyron.service` to `/etc/systemd/system/xyron.service`, adjust its `User`, `Group`, and paths if needed, then run:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now xyron
sudo systemctl status xyron
journalctl -u xyron -f
```

## Upgrade And Rollback

1. Stop the service/container.
2. Back up the database and `.env` securely.
3. Fetch the release/source and install pinned dependencies in the virtualenv or rebuild the image.
4. Run `alembic upgrade head`.
5. Start XYRON and inspect service logs and `/modules`.
6. On failure, stop XYRON and restore the database backup. Code rollback alone cannot reverse a schema migration safely.

Rotate the bot token through BotFather if exposed, update the secret file or deployment secret store, restart XYRON, and revoke the old token. Rotate database credentials through the database provider and update the service secret. Never include secrets in issue reports or logs.

## Resource And External-Service Notes

This release only uses the Telegram Bot API and SQL database; it has no AI, media-conversion, audio streaming, or group-call worker. No 1 GB RAM claim is made. Music voice chat typically requires a separate user-account session and group-call libraries with additional CPU/RAM and operational risk; this repository does not include or request those credentials. Do not treat metadata search as authorization to play catalog content. The bot token alone is not a voice-chat playback implementation.

## Current Feature Status

| Area | Status | Notes |
| --- | --- | --- |
| Startup/configuration | Implemented; unit-tested | Python 3.12+, environment config, basic logging, polling startup |
| Permissions | Implemented; mocked tests | Owner/admin/bot gates; live behavior needs private-group integration test |
| Moderation commands | Implemented; core tested | Ban/mute/kick families, unban/unmute, self-kick; requires Telegram integration testing |
| Duration parsing | Implemented; tested | Units s/m/h/d/w; max 366d; rejects invalid/negative/zero |
| Audit/warnings | Implemented; database-tested | Warnings, configurable threshold, one-day mute at threshold, event history |
| Temporary action recovery | Implemented; persistence-tested | Durable periodic retry loop; single-process worker assumption |
| Migrations | Implemented; clean SQLite tested | Initial schema migration; PostgreSQL migration needs environment-backed integration test |
| Help/module listing | Implemented | Lists only currently registered command set; feature groups not yet dynamically switchable |
| `/settings` eligibility inspection | Implemented; not live-tested | Groups observed in messages; current owner and bot permissions fetched live |
| Anti-spam, filters, locks | Not implemented | No flood detector or filtering actions are registered |
| Welcome, verification, notes, rules | Not implemented | No handlers or persistence workflow |
| Games, XP, economy, reputation | Not implemented | No game session engine or scoring |
| Music/group-call playback | Not implemented | No fake `/play`; requires separate compatible worker and external credentials |
| AI | Not implemented | No provider configured; no API keys required by current code |
| Media utilities | Not implemented | No file downloads or transformations |
| Reminders/RSS/polls | Not implemented | A reminder table is reserved; no scheduler command or notification logic exists |
| Docker/systemd | Configuration provided | Build/runtime deployment not tested against a live host |
| Live Telegram testing | Not performed | Requires operator credentials and a private test group |

## Troubleshooting

- **`BOT_TOKEN is required` / invalid token:** check local `.env` key spelling and rotate revoked tokens through BotFather. Do not paste the token into chat.
- **`OWNER_ID must be ... numeric`:** use the owner's numeric Telegram user ID, not `@s1dh7`.
- **Owner gate blocks moderation:** promote the configured owner to group admin; the bot will not moderate in that group otherwise.
- **Bot missing permission / `CHAT_ADMIN_REQUIRED`:** promote the bot and grant `Ban users`; `d*`/`s*` additionally need `Delete messages`.
- **Cannot resolve username:** the Bot API cannot look up arbitrary usernames. Have the member send a group message first or use a reply/numeric ID.
- **SQLite locked:** run one poller only, stop concurrent copies, and check file ownership. Use PostgreSQL for multiple application processes only after adding distributed job claiming.
- **Migration cannot connect:** verify `DATABASE_URL`, the async driver (`aiosqlite` or `asyncpg`), database availability, and network rules.
- **Temporary action still queued:** inspect service logs and current bot rights. Failed expirations are retained and retried; fixing Telegram permissions allows retry.
- **AI/music errors:** these modules are not implemented in this release; credentials will not make them work.