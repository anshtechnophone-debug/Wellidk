# XYRON Music plugin migration

The music plugin runs inside the existing XYRON process. It uses XYRON's Bot API polling loop and does not start a second bot client or register a separate /start handler.

## Retained behavior

- Songsx is the only search and selection source. YouTube and Jamendo are not used.
- Telethon player sessions power PyTgCalls voice playback.
- The existing player artwork, track chooser, colored inline controls, progress display, queue, pause/resume, skip, stop, replay and volume controls are retained.
- /addaccount and the legacy /newaccount alias connect voice-player accounts. /accounts lists them.
- XYRON's existing /start remains unchanged.
- The old account store and Songsx cache index are migration sources and are never deleted by this plugin.

## Environment

Add these settings to /etc/xyron/xyron.env:

```dotenv
MUSIC_ENABLED=true
MUSIC_API_ID=YOUR_TELEGRAM_API_ID
MUSIC_API_HASH=YOUR_TELEGRAM_API_HASH
MUSIC_OWNER_ID=YOUR_NUMERIC_TELEGRAM_USER_ID
MUSIC_SONGSX_BOT=Songsx_bot
MUSIC_CACHE_CHANNEL_ID=4363096287
MUSIC_CHANNEL=@xyronschannel
MUSIC_DATA_DIR=/opt/xyron/data/music
MUSIC_LEGACY_ACCOUNTS_FILE=/opt/wellidkmusic/data/player_accounts.json
MUSIC_LEGACY_CACHE_INDEX=/opt/wellidkmusic/media/songsx_cache.json
```

Use the same API ID/hash and cache channel ID as the old music bot. Never commit credentials to Git.

## Preserve existing accounts

The account JSON contains Telegram login credentials. It is not stored in Git and must be migrated on the VPS. Stop the old music service first so it does not use the same player sessions while XYRON starts.

Run this once from an administrative shell after backing up the old file:

```bash
systemctl stop wellidkmusic
mkdir -p /opt/xyron/data/music
if [ -f /opt/wellidkmusic/data/player_accounts.json ]; then
  cp -n /opt/wellidkmusic/data/player_accounts.json /opt/xyron/data/music/player_accounts.json
  chown xyron:xyron /opt/xyron/data/music/player_accounts.json
  chmod 600 /opt/xyron/data/music/player_accounts.json
fi
chown -R xyron:xyron /opt/xyron/data/music
chmod 700 /opt/xyron/data/music
```

The cp -n command will not overwrite an existing destination. Keep the old account file as a backup. The plugin can also read the legacy file if the new store is absent and the XYRON service user has permission to read it.

The legacy Songsx index is read from MUSIC_LEGACY_CACHE_INDEX. If a local audio file is unavailable, the plugin can restore it from the configured Telegram cache channel using the saved message ID. At least one connected player account must be able to read and post audio in that channel.

## Deploy and verify

1. Install the updated XYRON dependencies.
2. Confirm the old music service is stopped.
3. Ensure @xyronrobot can invite its voice-player account to music groups.
4. Ensure a connected player account can join those groups and post to the private cache channel.
5. Start XYRON and inspect logs for the number of loaded music player accounts.
6. In XYRON's private chat, run /accounts and verify the existing accounts appear.
7. In a test group, start a voice chat and test /play, result buttons, player controls, queue and repeat playback.

If /accounts shows zero accounts, do not delete the old store or add duplicate accounts. Check the old file path and file permissions first.

## Cache and speed

On first selection, Songsx supplies the audio; XYRON downloads it, plays it, stores a copy in the configured cache channel and indexes the search query, title and artist. Later matching searches consult the local index first and restore the Telegram-channel copy if the local file is missing. This avoids another Songsx round trip for indexed tracks. First-time searches still depend on Songsx response time and Telegram transfer speed.

The plugin reuses one HTTP connection pool for player cards and refreshes the progress display every three seconds.

