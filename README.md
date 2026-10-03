# ERLC Link

A clean Discord to ER:LC private-server management bot built with Python and discord.py.

## Features

- /config private button-based configuration panel
- Per-server ER:LC API key
- Role-based command access plus Administrator
- /server, /players, /command, /hint, /message, /kick, /ban, /unban
- Configurable prefix command router
- Example: !command hint Welcome to the server!
- Prefix trigger messages are deleted after a successful command when Manage Messages is available
- Discord action logs
- Optional Melonly webhook forwarding
- Configurable high-distance/high-kill detection
- Detection can log, kick, or ban
- SQLite configuration storage
- Secrets excluded from Git

## Extra quality-of-life features

- One private `/config` panel with ER:LC, permissions, detection, logs, and misc settings.
- Configurable command/admin roles.
- Discord audit logging for ER:LC actions.
- Optional Melonly webhook forwarding.
- Prefix trigger messages are automatically deleted after successful commands when the bot has permission.
- Common ER:LC command aliases such as `hint` → `h` and `message` → `m`.
- `cmd` works as a shorter alias for the configured prefix command.

## Detection

Default detector:

- enabled
- 250 studs minimum live distance
- 5 kills
- 5-second window
- action: log
- 60-second per-player cooldown

The detector polls ER:LC kill logs and current player positions. Kill logs identify the killer, victim and timestamp, while live player data can provide current location. The API does not provide a historical kill coordinate in the kill-log entry, so the detector intentionally uses the latest available live positions instead of pretending the distance is exact.

Start with log mode, tune the thresholds, and only then enable enforcement.

## Prefix

Default prefix is !.

Examples:

    !command hint Welcome everyone!
    !command m Server restart in 10 minutes!

The prefix is configured from /config -> Misc.

## Melonly

The public Melonly Python client documents API authentication and log read endpoints. This bot does not guess at an undocumented log-write API. Instead, the config supports a Melonly-provided incoming webhook URL for moderation and detection events.

## Setup

1. Install Python 3.11 or newer.
2. Copy .env.example to .env.
3. Put your Discord bot token in DISCORD_TOKEN.
4. Run: pip install -r requirements.txt
5. Run: python bot.py

## Discord permissions

View Channels, Send Messages, Embed Links, Read Message History, and Manage Messages.

Enable Message Content Intent and Server Members Intent in the Discord Developer Portal.

## ER:LC API

Put the private server key into /config -> ER:LC. Never commit the key to GitHub.

## Commands

| Command | Purpose |
|---|---|
| /config | Configure the bot |
| /server | Server information |
| /players | Current players |
| /command | Execute an ER:LC command |
| /hint | Send :h |
| /message | Send :m |
| /kick | Kick a player |
| /ban | Ban a player |
| /unban | Unban a player |

## Security

Never commit .env, database files, Discord tokens, ER:LC keys, or Melonly tokens.


## Server ID

The optional `SERVER_ID` setting is your Discord server ID. It is used to sync slash commands to one server for faster testing. Leave it blank if you want global slash-command sync.

Example:

```env
SERVER_ID=123456789012345678
```

To copy it in Discord, enable **Developer Mode**, right-click your server, and choose **Copy Server ID**.
