# ER:LC Link

A clean Discord-to-ER:LC private-server management bot built with Python, discord.py, SQLite and Discord Components V2.

## Features

- **Components V2 /config Control Center**
- Same-message tab switching
- Current tab button is disabled
- Private/ephemeral configuration panel
- ER:LC server-key configuration
- Command-role and admin-role configuration
- Configurable prefix
- Prefix-only ER:LC action router
- `command` and `cmd` aliases
- `hint` / `message` convenience aliases
- `/server`, `/players`, `/help`
- High-distance/high-kill anti-cheat detection
- Detection actions: **Log**, **Log Kick**, **Log Ban**
- Discord logging
- Optional Melonly webhook logging
- Melonly receives **only actual bot kick/ban events**
- SQLite configuration storage
- Docker/Procfile deployment support

## /config

Open:

`/config`

The panel has:

### Overview
Shows the current configuration at a glance.

### ER:LC
Configure the private ER:LC server key.

### Permissions
- ER:LC command role
- Admin/config role
- Discord Administrators always have access

### Detection
Configure:

- Enabled
- Distance in studs
- Kill threshold
- Detection window
- Cooldown
- Action

The action selector contains exactly:

- **Log** — detect and log only
- **Log Kick** — detect, log, then kick
- **Log Ban** — detect, log, then ban

### Logs
Configure:

- Discord log channel
- Melonly API token
- Melonly incoming webhook

Melonly logging is intentionally limited to kick and ban events actually issued by this bot.

### Misc
Configure the prefix.

## Prefix commands

ER:LC actions are intentionally prefix-only.

Default prefix:

`!`

Examples:

```
!command hint Welcome everyone!
!command message Server restart soon!
!command kick Player Reason
!command ban Player Reason
!command unban Player
!command <raw ERLC command> <arguments>
```

Short alias:

```
!cmd kick Player Reason
```

The prefix trigger is deleted after a successful command when the bot has **Manage Messages** permission.

For prefix **kick** and **ban**:

- The reason sent to the logger is the staff-entered reason.
- The log description identifies who ran the command.
- Melonly receives the kick/ban event only after the ER:LC command succeeds.

Normal ER:LC commands such as hint, message, unban, team, tp, etc. are not sent to Melonly.

## Slash commands

| Command | Purpose |
|---|---|
| `/config` | Open the Components V2 configuration center |
| `/server` | Show ER:LC server information |
| `/players` | Show current ER:LC players |
| `/help` | Show the command guide |

## Anti-cheat

The detector polls ER:LC kill logs and current player positions.

Default values:

- Enabled: Yes
- Distance: 250 studs
- Kills: 5
- Window: 5 seconds
- Action: Log
- Cooldown: 60 seconds

The detector uses the latest available live player positions. ER:LC kill-log entries do not provide a historical kill coordinate, so the detector does not claim that the live distance is an exact historical distance.

Start with **Log**, tune the thresholds, then enable enforcement.

## Melonly

This project supports a configurable Melonly incoming webhook.

The bot does **not** send every ER:LC command to Melonly.

Melonly events are limited to:

- Bot-issued kick
- Bot-issued ban

A prefix kick/ban identifies the Discord staff member who ran it.

An automatic detection kick/ban identifies **ER:LC Link Anti-Cheat** as the executor and includes the detection reason.

## Setup

1. Install Python 3.12 or newer.
2. Copy `.env.example` to `.env`.
3. Put your Discord bot token in `DISCORD_TOKEN`.
4. Optionally put your Discord server ID in `SERVER_ID`.
5. Install dependencies:

```
pip install -r requirements.txt
```

6. Start the bot:

```
python bot.py
```

## .env

Example:

```env
DISCORD_TOKEN=YOUR_DISCORD_BOT_TOKEN
SERVER_ID=1556036425831419904
DATABASE_PATH=data/erlc_link.db
```

`SERVER_ID` is the **Discord server ID**, not the ER:LC server key.

If `SERVER_ID` is set and the bot is in that server, slash commands are synced there for fast testing. If it is missing or the bot is not in that server, the bot syncs to its connected Discord servers instead.

Never put the ER:LC server key, Discord token, or Melonly credentials in GitHub.

## Discord permissions

Recommended bot permissions:

- View Channels
- Send Messages
- Embed Links
- Read Message History
- Manage Messages

The bot also needs the appropriate ER:LC permissions through its configured ER:LC private server/API setup.

In the Discord Developer Portal, enable:

- Message Content Intent
- Server Members Intent

## Deployment

### Local

```
python bot.py
```

### Docker

```
docker build -t erlc-link .
docker run --env-file .env erlc-link
```

### Procfile

```
worker: python bot.py
```

## Security

Never commit:

- `.env`
- Discord bot tokens
- ER:LC server keys
- Melonly tokens
- Database files

