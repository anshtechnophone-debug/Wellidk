# XYRON - One Bot. Every Feature.

XYRON is an asynchronous Telegram bot. This release provides live permission-checked moderation, restart-safe mute/ban expiration, report review, group policies (rules, notes, filters, locks, welcome/goodbye, and opt-in flood protection), categorized inline help, 13 local games, and an optional event-driven AI assistant.

**This is not yet the full superbot described in the specification.** The feature-status matrix below is authoritative. The current catalogue contains 95 real registered commands in 26 categories, below the requested 150–200. The local game suite is implemented and needs no AI credits. Music playback, economy/progression, media processing, RSS/recurring reminders, and AI auto-enforcement remain incomplete.

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

### Termux (Android)

XYRON uses polling, SQLite, and no native media or group-call libraries, so the default install is suitable for Termux. PostgreSQL support is optional and is not installed for the SQLite setup.

```bash
pkg update
pkg install python git
git clone <repository-url> xyron
cd xyron
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
cp .env.example .env
nano .env
set -a; source .env; set +a
alembic upgrade head
python -m app
```

PostgreSQL uses the already pinned `asyncpg` dependency; set `DATABASE_URL` to its async URL. Keep Termux awake while polling; Android may suspend the process. No live Termux device test was performed.

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
| `/health` | Check database and background worker status | Anyone |
| `/settings` | Inspect known groups, owner status, and current bot permissions | Configured owner only |
| `/ban <reply|id|known_username> [reason]` | Ban | Actor admin + bot `Ban users` + owner admin |
| `/dban ...` | Ban and attempt to delete the replied-to message | Same plus `Delete messages` |
| `/sban ...` | Ban and silently delete command | Same plus `Delete messages` |
| `/tban <reply|id|known_username> <duration> [reason]` | Durable temporary ban | Same as `/ban` |
| `/unban <reply|id|known_username>` | Unban and cancel pending temporary action | Actor admin + bot `Ban users` + owner admin |
| `/banlist` | List currently tracked bans from moderation history | Actor admin + owner admin |
| `/mute <reply|id|known_username> [duration] [reason]` | Restrict sending; optional duration such as `10m` | Actor admin + bot `Ban users` + owner admin |
| `/dmute ...` | Mute and attempt to delete replied-to message | Same plus `Delete messages` |
| `/smute ...` | Mute and silently delete command | Same plus `Delete messages` |
| `/tmute <reply|id|known_username> <duration> [reason]` | Durable temporary restriction | Same as `/mute` |
| `/unmute <reply|id|known_username>` | Restore saved pre-mute rights intersected with current group defaults | Actor admin + bot `Ban users` + owner admin |
| `/mutelist` | List XYRON-tracked saved mute baselines | Actor admin + owner admin |
| `/kick <reply|id|known_username> [reason]` | Remove via ban then unban | Actor admin + bot `Ban users` + owner admin |
| `/dkick ...` | Remove and attempt to delete replied-to message | Same plus `Delete messages` |
| `/skick ...` | Remove and silently delete command | Same plus `Delete messages` |
| `/kickme` | Remove the caller only | Any member; owner and bot gates apply |
| `/warn <reply|id|known_username> [reason]` | Record warning; default threshold applies a one-day mute | Actor admin + bot `Ban users` + owner admin |
| `/warns <reply|id|known_username>` | Count current warnings | Actor admin + owner admin |
| `/resetwarn <reply|id|known_username>` | Clear warnings | Actor admin + owner admin |
| `/setwarnlimit <1-20>` | Change group threshold | Actor admin + owner admin |
| `/modlog [1-25]` | Read recent moderation events | Actor admin + owner admin |
| `/modstats` | Summarize moderation actions in the last 30 days | Actor admin + owner admin |
| `/adminlist` | Fetch current Telegram administrators | Actor admin + owner admin |
| `/report [reason]` | Reply to a message; send a report privately to admins who started the bot | Anyone |
| `/reports` | List open reports for this group | Actor admin + owner admin |
| `/reviewreport <id> <reviewed|dismissed|actioned>` | Update a same-group report status | Actor admin + owner admin |
| `/appeal <message>` | Submit a request for review without changing moderation | Anyone in the affected group |
| `/appeals` | List open appeals in this group | Actor admin + owner admin |
| `/reviewappeal <id> <accepted|rejected|needs_info> [note]` | Review an appeal; does not automatically change moderation | Actor admin + owner admin |
| `/wordseek [4|5|6]` | Start a local Word Seek game | Anyone |
| `/guess <word>` | Guess in your active game | Anyone; game owner only |
| `/wordcancel` | Cancel your active game | Anyone; game owner only |
| `/wordrank` | Show persistent chat scores | Anyone |
| `/remind <duration> <message>` | Set a durable reminder in this chat | Anyone |
| `/reminders` | List your pending reminders | Anyone; creator only |
| `/cancelreminder <id>` | Cancel your own pending reminder | Anyone; creator only |

The inline `/help` menu is the complete current command catalogue: **95 registered commands in 26 categories**. It includes 8-command pages, concrete examples, and permission labels. This remains below the requested 150–200 commands. Many requested feature families are intentionally not represented as commands until implemented.

The generated [full command reference](docs/command-reference.md) lists every currently registered command with its category, example, and access level.

Warn threshold action is currently a one-day mute. Although a `warn_action` database field exists, no command changes it; warning-limit ban selection is not exposed yet. `/mute <target> <duration>` is a timed mute; `/tmute` remains supported.

### Silent/Delete Semantics

`s*` means remove the command message after a successful operation. `d*` means delete the replied-to target message after the operation. Deletion failure is reported where a response remains possible. A successful moderation action is not misreported as failed if only message deletion failed. API failure does not create a success audit event.

## Data And Migrations

SQLAlchemy models cover groups, group policies, observed users, moderation events, temporary actions, saved pre-mute permissions, warnings, reports, AI usage, Word Seek and casual game sessions/scores, and durable one-shot reminders. IDs use 64-bit integer columns. Timestamps are validated as timezone-aware and normalized to UTC; SQLite timestamps are restored as UTC-aware values on reads.

Apply migrations with `alembic upgrade head`. Back up before upgrades. The initial downgrade drops application tables and therefore destroys their data; do not run `alembic downgrade base` on production unless you intentionally want that result. The current app startup creates missing tables for convenience but does not automatically evolve an existing schema; run Alembic for upgrades. Revisions through `0008_appeals` add mute snapshots, Word Seek/casual games, AI usage, reports, group policies, and appeals.

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

The default release needs only the Telegram Bot API and SQL database. AI requests are optional and require an OpenAI-compatible Chat Completions endpoint, credentials, an explicit enable setting, and disabling the global emergency stop. Persistent request/token limits are enforced, but costs are not promised to be zero. Word Seek is local and uses no AI credits. No 1 GB RAM claim is made. Music voice chat typically requires a separate user-account session and group-call libraries with additional CPU/RAM and operational risk; this repository does not include or request those credentials. Do not treat metadata search as authorization to play catalog content. The bot token alone is not a voice-chat playback implementation.

### AI Setup And Privacy

AI is disabled by default. Set `AI_ENABLED=true`, `AI_GLOBAL_EMERGENCY_STOP=false`, `AI_PROVIDER=openai_compatible`, `AI_MODEL`, and `AI_API_KEY` in the private environment file. `AI_BASE_URL` can target another OpenAI-compatible endpoint; HTTPS is required except on localhost. Group context is retained in a bounded in-memory buffer and sent only for direct addresses/replies, addressed moderation events, or reports. Configure Telegram privacy so XYRON receives only the group updates it is intended to observe. The Bot API cannot fetch arbitrary history. AI responses and incident summaries are advisory; the AI cannot execute bans, mutes, or arbitrary Telegram tools.

## Current Feature Status

| Area | Status | Notes |
| --- | --- | --- |
| Startup/configuration | Implemented; unit-tested | Python 3.12+, environment config, basic logging, polling startup |
| Permissions | Implemented; mocked tests | Owner/admin/bot gates; live behavior needs private-group integration test |
| Moderation commands | Implemented; core tested | Ban/mute/kick families, unban/unmute, self-kick; requires Telegram integration testing |
| Duration parsing | Implemented; tested | Units s/m/h/d/w; max 366d; rejects invalid/negative/zero |
| Audit/warnings | Implemented; database-tested | Warnings, configurable threshold, one-day mute at threshold, event history |
| Temporary action recovery | Implemented; persistence-tested | Durable periodic retry loop; single-process worker assumption |
| Migrations | Implemented; clean SQLite tested | Revisions through 0007; PostgreSQL migration needs environment-backed integration test |
| Help/module listing | Implemented; tested | Inline category menu, validated callbacks, 95 actual commands in 26 categories; pagination supported |
| Health check | Implemented; tested | Database SELECT and background worker task state |
| `/settings` eligibility inspection | Implemented; not live-tested | Groups observed in messages; current owner and bot permissions fetched live |
| Anti-spam, filters, locks | Partial; mocked tests | Explicit opt-in flood/repeat detection, link/type locks, saved responses and approvals; deletion only, no escalation |
| Welcome, notes, rules | Implemented; database tested | Safe escaped templates, per-group rules/notes; CAPTCHA and verification are not implemented |
| Word Seek | Implemented; unit/database tested | 4/5/6-letter pools, feedback, per-chat/player sessions, timeout/cancel, persistent scoring and leaderboard |
| Local games | Implemented; unit/database tested | 13: Word Seek, Hangman, Scramble, Anagram, Word Chain, Trivia, True/False, Riddle, Quick Math, Number Guess, RPS, Tic-Tac-Toe, Team Word |
| XP/economy/reputation | Not implemented | No progression/economy privileges or reputation handlers |
| Music/group-call playback | Not implemented | No fake `/play`; requires separate compatible worker and external credentials |
| AI chat and incident analysis | Implemented; mocked tests; disabled by default | OpenAI-compatible Chat Completions, direct-address only, bounded memory, persistent usage budgets, report/incident summaries; advisory only |
| Owner natural-language control | Partial; parser tests | Explicit owner-only ban/mute/unmute subset; reuses existing authorization; ambiguous/unsupported text rejected |
| AI enforcement tools | Not implemented | AI cannot execute moderation; analyses are advisory only |
| Media utilities | Not implemented | No file downloads or transformations |
| Personal reminders | Implemented; persistence/worker tested | One-shot UTC reminders, creator-scoped list/cancel, marked before delivery to prevent restart duplicates; no recurring schedules |
| Appeals | Implemented; repository tested | Group-scoped, rate-limited submissions and explicit admin review; no automatic moderation change |
| RSS/polls | Not implemented | No feed or poll handlers |
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
- **AI errors:** AI is disabled by default. Check provider URL/model, the emergency stop, and per-user/group/daily limits. It never executes moderation.
- **Music errors:** group-call playback is not implemented; adding credentials will not enable it.