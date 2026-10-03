import asyncio
import json
import logging
import math
import os
import shlex
import time
from collections import defaultdict, deque
from dataclasses import dataclass, asdict
from typing import Optional

import aiohttp
import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("erlc-link")

DB_PATH = os.getenv("DATABASE_PATH", "data/erlc_link.db")
ERLC_BASE = "https://api.policeroleplay.community/v2"


@dataclass
class GuildConfig:
    guild_id: int
    erlc_server_key: str = ""
    prefix: str = "!"
    command_role_id: int = 0
    admin_role_id: int = 0
    log_channel_id: int = 0
    melonly_api_token: str = ""
    melonly_webhook_url: str = ""
    detection_enabled: bool = True
    detection_distance: float = 250.0
    detection_kills: int = 5
    detection_window: float = 5.0
    detection_action: str = "log"
    detection_cooldown: float = 60.0


async def init_db():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS guild_config "
            "(guild_id INTEGER PRIMARY KEY, data TEXT NOT NULL)"
        )
        await db.commit()


async def get_config(guild_id: int) -> GuildConfig:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT data FROM guild_config WHERE guild_id = ?", (guild_id,)
        )
        row = await cursor.fetchone()
        await cursor.close()

    if not row:
        cfg = GuildConfig(guild_id=guild_id)
        await save_config(cfg)
        return cfg

    try:
        data = json.loads(row[0])
        data.pop("guild_id", None)
        return GuildConfig(guild_id=guild_id, **data)
    except Exception:
        cfg = GuildConfig(guild_id=guild_id)
        await save_config(cfg)
        return cfg


async def save_config(cfg: GuildConfig):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO guild_config(guild_id, data) VALUES(?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET data=excluded.data",
            (cfg.guild_id, json.dumps(asdict(cfg))),
        )
        await db.commit()


class ERLCClient:
    def __init__(self, server_key: str, session: aiohttp.ClientSession):
        self.server_key = server_key
        self.session = session

    @property
    def headers(self):
        return {
            "Server-Key": self.server_key,
            "Accept": "application/json",
        }

    async def request(self, method: str, path: str, **kwargs):
        if not self.server_key:
            raise RuntimeError("ER:LC server key is not configured.")

        url = f"{ERLC_BASE}{path}"
        timeout = aiohttp.ClientTimeout(total=15)

        async with self.session.request(
            method, url, headers=self.headers, timeout=timeout, **kwargs
        ) as response:
            text = await response.text()

            if response.status >= 400:
                raise RuntimeError(
                    f"ER:LC API returned HTTP {response.status}: {text[:300]}"
                )

            if not text:
                return {}

            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return {"raw": text}

    async def server(self):
        return await self.request("GET", "/server")

    async def players(self):
        data = await self.request("GET", "/server?Players=true")
        return data.get("Players", [])

    async def kill_logs(self):
        data = await self.request("GET", "/server?KillLogs=true")
        return data.get("KillLogs", [])

    async def command(self, command: str):
        return await self.request(
            "POST",
            "/server/command",
            json={"Command": command},
        )


class MelonlyLogger:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    async def send(self, cfg: GuildConfig, payload: dict):
        if not cfg.melonly_webhook_url:
            return False

        try:
            async with self.session.post(
                cfg.melonly_webhook_url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                return 200 <= response.status < 300
        except Exception:
            log.exception("Melonly webhook failed")
            return False


class Permission:
    @staticmethod
    def can_manage(member: discord.Member, cfg: GuildConfig) -> bool:
        if member.guild_permissions.administrator:
            return True

        if cfg.command_role_id:
            return any(role.id == cfg.command_role_id for role in member.roles)

        return False

    @staticmethod
    def can_config(member: discord.Member, cfg: GuildConfig) -> bool:
        if member.guild_permissions.administrator:
            return True

        return bool(
            cfg.admin_role_id
            and any(role.id == cfg.admin_role_id for role in member.roles)
        )


def player_field(player: dict, *names, default=None):
    for name in names:
        if name in player:
            return player[name]
    return default


def player_id(player: dict):
    value = player_field(
        player,
        "UserId",
        "userId",
        "RobloxId",
        "roblox_id",
    )

    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def player_name(player: dict):
    return str(
        player_field(
            player,
            "Player",
            "Username",
            "username",
            default="Unknown",
        )
    )


def player_position(player: dict):
    pos = player_field(
        player,
        "Position",
        "position",
        "Location",
        "location",
    )

    if isinstance(pos, dict):
        x = pos.get("x", pos.get("X"))
        z = pos.get("z", pos.get("Z"))

        if x is not None and z is not None:
            try:
                return float(x), float(z)
            except (TypeError, ValueError):
                return None

    if isinstance(pos, (list, tuple)) and len(pos) >= 2:
        try:
            return float(pos[0]), float(pos[-1])
        except (TypeError, ValueError):
            return None

    return None


def distance(a, b):
    if not a or not b:
        return None

    return math.sqrt(
        (a[0] - b[0]) ** 2 +
        (a[1] - b[1]) ** 2
    )


class ConfigView(discord.ui.View):
    def __init__(self, bot, cfg: GuildConfig, owner_id: int):
        super().__init__(timeout=300)

        self.bot = bot
        self.cfg = cfg
        self.owner_id = owner_id
        self.page = "overview"

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Only the person who opened this panel can use it.",
                ephemeral=True,
            )
            return False

        return True

    def rebuild(self):
        self.clear_items()

        pages = [
            ("Overview", "overview"),
            ("ER:LC", "erlc"),
            ("Permissions", "permissions"),
            ("Detection", "detection"),
            ("Logs", "logs"),
            ("Misc", "misc"),
        ]

        for label, value in pages:
            self.add_item(
                PageButton(
                    label,
                    value,
                    self.page == value,
                )
            )

        self.add_item(EditButton())
        self.add_item(CloseButton())

    def embed(self):
        embed = discord.Embed(
            title="ER:LC Link • Configuration",
            color=discord.Color.blurple(),
        )

        embed.set_footer(text="Changes are saved immediately.")

        if self.page == "overview":
            embed.description = (
                "Configure your ER:LC connection, command access, "
                "anti-cheat detector, logs and prefix."
            )

        elif self.page == "erlc":
            embed.add_field(
                name="Server Key",
                value=(
                    "Configured"
                    if self.cfg.erlc_server_key
                    else "Not configured"
                ),
                inline=False,
            )
            embed.add_field(
                name="API",
                value=ERLC_BASE,
                inline=False,
            )

        elif self.page == "permissions":
            embed.add_field(
                name="Command Role",
                value=(
                    f"<@&{self.cfg.command_role_id}>"
                    if self.cfg.command_role_id
                    else "Not configured"
                ),
            )
            embed.add_field(
                name="Admin Role",
                value=(
                    f"<@&{self.cfg.admin_role_id}>"
                    if self.cfg.admin_role_id
                    else "Administrator only"
                ),
            )
            embed.description = (
                "Discord Administrators always have access. "
                "The configured command role can run ER:LC commands."
            )

        elif self.page == "detection":
            embed.add_field(
                name="Enabled",
                value="Yes" if self.cfg.detection_enabled else "No",
            )
            embed.add_field(
                name="Distance",
                value=f"{self.cfg.detection_distance:.0f} studs",
            )
            embed.add_field(
                name="Kills",
                value=str(self.cfg.detection_kills),
            )
            embed.add_field(
                name="Window",
                value=f"{self.cfg.detection_window:g}s",
            )
            embed.add_field(
                name="Action",
                value=self.cfg.detection_action.upper(),
            )
            embed.add_field(
                name="Cooldown",
                value=f"{self.cfg.detection_cooldown:g}s",
            )

            embed.description = (
                "The detector observes kill logs and compares the "
                "latest available live positions."
            )

        elif self.page == "logs":
            embed.add_field(
                name="Discord Log Channel",
                value=(
                    f"<#{self.cfg.log_channel_id}>"
                    if self.cfg.log_channel_id
                    else "Not configured"
                ),
            )
            embed.add_field(
                name="Melonly API Token",
                value=(
                    "Configured"
                    if self.cfg.melonly_api_token
                    else "Not configured"
                ),
            )
            embed.add_field(
                name="Melonly Webhook",
                value=(
                    "Configured"
                    if self.cfg.melonly_webhook_url
                    else "Not configured"
                ),
            )

        elif self.page == "misc":
            embed.add_field(
                name="Prefix",
                value=self.cfg.prefix,
            )
            embed.add_field(
                name="Examples",
                value=(
                    f"{self.cfg.prefix}command hint Welcome!\\n"
                    f"{self.cfg.prefix}command kick Player Reason\\n"
                    f"{self.cfg.prefix}command ban Player Reason\\n"
                    f"{self.cfg.prefix}command unban Player"
                ),
                inline=False,
            )
            embed.add_field(
                name="Aliases",
                value=f"{self.cfg.prefix}command or {self.cfg.prefix}cmd",
                inline=False,
            )

        return embed


class PageButton(discord.ui.Button):
    def __init__(self, label, value, active):
        super().__init__(
            label=label,
            style=(
                discord.ButtonStyle.primary
                if active
                else discord.ButtonStyle.secondary
            ),
            custom_id=f"cfg_page_{value}",
            disabled=active,
        )

        self.value = value

    async def callback(self, interaction):
        view: ConfigView = self.view
        view.page = self.value
        view.rebuild()

        await interaction.response.edit_message(
            embed=view.embed(),
            view=view,
        )


class EditButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Edit",
            style=discord.ButtonStyle.success,
            custom_id="cfg_edit",
        )

    async def callback(self, interaction):
        view: ConfigView = self.view
        modal = ConfigModal(
            view.cfg,
            view.page,
        )

        await interaction.response.send_modal(modal)


class CloseButton(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="Close",
            style=discord.ButtonStyle.danger,
            custom_id="cfg_close",
        )

    async def callback(self, interaction):
        await interaction.response.edit_message(
            content="Configuration panel closed.",
            embed=None,
            view=None,
        )


class ConfigModal(discord.ui.Modal):
    def __init__(self, cfg: GuildConfig, page: str):
        super().__init__(title=f"Edit {page.title()}")

        self.cfg = cfg
        self.page = page

        if page == "erlc":
            self.key = discord.ui.TextInput(
                label="ER:LC Server API Key",
                required=True,
                placeholder="Paste your private server key",
            )
            self.add_item(self.key)

        elif page == "permissions":
            self.command_role = discord.ui.TextInput(
                label="Command Role ID",
                required=False,
                placeholder="Discord role ID",
                default=str(cfg.command_role_id or ""),
            )
            self.admin_role = discord.ui.TextInput(
                label="Admin Role ID",
                required=False,
                placeholder="Discord role ID",
                default=str(cfg.admin_role_id or ""),
            )
            self.add_item(self.command_role)
            self.add_item(self.admin_role)

        elif page == "detection":
            self.enabled = discord.ui.TextInput(
                label="Enabled true/false",
                default=str(cfg.detection_enabled).lower(),
            )
            self.distance_input = discord.ui.TextInput(
                label="Distance in studs",
                default=str(int(cfg.detection_distance)),
            )
            self.kills = discord.ui.TextInput(
                label="Kills threshold",
                default=str(cfg.detection_kills),
            )
            self.window = discord.ui.TextInput(
                label="Window in seconds",
                default=str(cfg.detection_window),
            )
            self.action = discord.ui.TextInput(
                label="Action log, kick, or ban",
                default=cfg.detection_action,
            )

            for item in (
                self.enabled,
                self.distance_input,
                self.kills,
                self.window,
                self.action,
            ):
                self.add_item(item)

        elif page == "logs":
            self.channel = discord.ui.TextInput(
                label="Discord Log Channel ID",
                required=False,
                default=str(cfg.log_channel_id or ""),
            )
            self.melonly_token = discord.ui.TextInput(
                label="Melonly API Token",
                required=False,
                default=cfg.melonly_api_token,
            )
            self.melonly_webhook = discord.ui.TextInput(
                label="Melonly Log Webhook URL",
                required=False,
                default=cfg.melonly_webhook_url,
            )

            self.add_item(self.channel)
            self.add_item(self.melonly_token)
            self.add_item(self.melonly_webhook)

        elif page == "misc":
            self.prefix = discord.ui.TextInput(
                label="Prefix",
                default=cfg.prefix,
                max_length=3,
            )
            self.add_item(self.prefix)

    async def on_submit(self, interaction):
        try:
            if self.page == "erlc":
                self.cfg.erlc_server_key = self.key.value.strip()

            elif self.page == "permissions":
                self.cfg.command_role_id = (
                    int(self.command_role.value)
                    if self.command_role.value.strip()
                    else 0
                )
                self.cfg.admin_role_id = (
                    int(self.admin_role.value)
                    if self.admin_role.value.strip()
                    else 0
                )

            elif self.page == "detection":
                self.cfg.detection_enabled = (
                    self.enabled.value.strip().lower()
                    in {"true", "yes", "on", "1"}
                )

                self.cfg.detection_distance = max(
                    1,
                    float(self.distance_input.value),
                )

                self.cfg.detection_kills = max(
                    2,
                    int(self.kills.value),
                )

                self.cfg.detection_window = max(
                    1,
                    float(self.window.value),
                )

                action = self.action.value.strip().lower()

                if action not in {"log", "kick", "ban"}:
                    raise ValueError(
                        "Action must be log, kick, or ban."
                    )

                self.cfg.detection_action = action

            elif self.page == "logs":
                self.cfg.log_channel_id = (
                    int(self.channel.value)
                    if self.channel.value.strip()
                    else 0
                )
                self.cfg.melonly_api_token = (
                    self.melonly_token.value.strip()
                )
                self.cfg.melonly_webhook_url = (
                    self.melonly_webhook.value.strip()
                )

            elif self.page == "misc":
                prefix = self.prefix.value.strip()

                if not prefix:
                    raise ValueError("Prefix cannot be empty.")

                self.cfg.prefix = prefix

            await save_config(self.cfg)

            await interaction.response.send_message(
                "Configuration saved.",
                ephemeral=True,
            )

        except ValueError as exc:
            await interaction.response.send_message(
                f"Invalid configuration: {exc}",
                ephemeral=True,
            )


class ERLCBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True

        super().__init__(
            command_prefix=self.dynamic_prefix,
            intents=intents,
            help_command=None,
        )

        self.http_session: Optional[aiohttp.ClientSession] = None
        self.melonly: Optional[MelonlyLogger] = None
        self.kill_seen = set()
        self.kill_times = defaultdict(deque)
        self.detection_cooldowns = {}

    async def dynamic_prefix(self, bot, message):
        if not message.guild:
            return "!"

        cfg = await get_config(message.guild.id)
        return cfg.prefix

    async def setup_hook(self):
        await init_db()

        self.http_session = aiohttp.ClientSession()
        self.melonly = MelonlyLogger(self.http_session)

        self.detection_loop.start()

        dev_guild = os.getenv("DEV_GUILD_ID")

        if dev_guild:
            await self.tree.sync(
                guild=discord.Object(id=int(dev_guild))
            )
            log.info(
                "Synced slash commands to development guild %s",
                dev_guild,
            )
        else:
            await self.tree.sync()
            log.info("Synced global slash commands")

    async def close(self):
        self.detection_loop.cancel()

        if self.http_session:
            await self.http_session.close()

        await super().close()

    async def get_erlc(self, guild_id):
        cfg = await get_config(guild_id)

        if not cfg.erlc_server_key:
            raise RuntimeError(
                "ER:LC API key is not configured. "
                "Open /config -> ER:LC."
            )

        return (
            ERLCClient(
                cfg.erlc_server_key,
                self.http_session,
            ),
            cfg,
        )

    async def log_action(
        self,
        cfg,
        guild,
        title,
        description,
        color=discord.Color.blurple(),
    ):
        embed = discord.Embed(
            title=title,
            description=description,
            color=color,
            timestamp=discord.utils.utcnow(),
        )

        if cfg.log_channel_id:
            channel = guild.get_channel(cfg.log_channel_id)

            if channel:
                try:
                    await channel.send(embed=embed)
                except Exception:
                    log.exception(
                        "Discord log failed for guild %s",
                        guild.id,
                    )

        if self.melonly:
            await self.melonly.send(
                cfg,
                {
                    "source": "ERLC Link",
                    "guildId": guild.id,
                    "type": title.lower().replace(" ", "_"),
                    "text": description,
                    "timestamp": int(time.time()),
                },
            )


bot = ERLCBot()


def require_manage():
    async def predicate(interaction):
        cfg = await get_config(interaction.guild.id)

        if not isinstance(interaction.user, discord.Member):
            raise app_commands.CheckFailure(
                "This command can only be used in a server."
            )

        if not Permission.can_manage(interaction.user, cfg):
            raise app_commands.CheckFailure(
                "You do not have permission to run ER:LC commands."
            )

        return True

    return app_commands.check(predicate)


def require_config():
    async def predicate(interaction):
        cfg = await get_config(interaction.guild.id)

        if not isinstance(interaction.user, discord.Member):
            raise app_commands.CheckFailure(
                "This command can only be used in a server."
            )

        if not Permission.can_config(interaction.user, cfg):
            raise app_commands.CheckFailure(
                "You need Administrator or the configured admin role."
            )

        return True

    return app_commands.check(predicate)


@bot.tree.command(
    name="config",
    description="Open the ERLC Link configuration panel.",
)
@require_config()
async def config_command(interaction):
    cfg = await get_config(interaction.guild.id)

    view = ConfigView(
        bot,
        cfg,
        interaction.user.id,
    )

    view.rebuild()

    await interaction.response.send_message(
        embed=view.embed(),
        view=view,
        ephemeral=True,
    )


@bot.tree.command(
    name="help",
    description="Show ER:LC Link commands and examples.",
)
async def help_command(interaction):
    cfg = await get_config(interaction.guild.id)
    embed = discord.Embed(
        title="ER:LC Link • Command Guide",
        description=(
            f"ER:LC actions are prefix-only. Use **{cfg.prefix}command** "
            "followed by the ER:LC command.\\n\\n"
            f"Hint: {cfg.prefix}command hint Welcome!\\n"
            f"Message: {cfg.prefix}command message Server restart soon!\\n"
            f"Kick: {cfg.prefix}command kick Player Reason\\n"
            f"Ban: {cfg.prefix}command ban Player Reason\\n"
            f"Unban: {cfg.prefix}command unban Player\\n"
            f"Raw: {cfg.prefix}command <erlc command> <arguments>\\n\\n"
            "The cmd alias also works."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Slash Commands",
        value="/config • /server • /players • /help",
        inline=False,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(
    name="server",
    description="Show ER:LC server information.",
)
@require_manage()
async def server_command(interaction):
    await interaction.response.defer(ephemeral=True)

    try:
        client, _ = await bot.get_erlc(interaction.guild.id)
        data = await client.server()

        embed = discord.Embed(
            title="ER:LC Server",
            color=discord.Color.blurple(),
        )

        embed.add_field(
            name="Name",
            value=data.get("Name", "Unknown"),
        )

        embed.add_field(
            name="Players",
            value=(
                f"{data.get('CurrentPlayers', '?')}/"
                f"{data.get('MaxPlayers', '?')}"
            ),
        )

        await interaction.followup.send(
            embed=embed,
            ephemeral=True,
        )

    except Exception as exc:
        await interaction.followup.send(
            f"ER:LC error: {exc}",
            ephemeral=True,
        )


@bot.tree.command(
    name="players",
    description="Show players currently in the ER:LC server.",
)
@require_manage()
async def players_command(interaction):
    await interaction.response.defer(ephemeral=True)

    try:
        client, _ = await bot.get_erlc(interaction.guild.id)
        players = await client.players()

        if not players:
            await interaction.followup.send(
                "No players found or the server is empty.",
                ephemeral=True,
            )
            return

        lines = []

        for p in players[:30]:
            lines.append(
                f"{player_name(p)} — {player_id(p)}"
            )

        embed = discord.Embed(
            title=f"ER:LC Players • {len(players)} online",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )

        await interaction.followup.send(
            embed=embed,
            ephemeral=True,
        )

    except Exception as exc:
        await interaction.followup.send(
            f"ER:LC error: {exc}",
            ephemeral=True,
        )


# ER:LC action commands are intentionally prefix-only.
# Use: !command <erlc-command> <arguments>
# Examples: !command hint Welcome! / !command kick Player Reason

async def process_prefix(message):
    if not message.guild or message.author.bot:
        return

    cfg = await get_config(message.guild.id)

    if not message.content.startswith(cfg.prefix):
        return

    raw = message.content[len(cfg.prefix):].strip()
    if not raw:
        return

    try:
        parts = shlex.split(raw)
    except ValueError:
        parts = raw.split()

    if not parts:
        return

    command_name = parts.pop(0).lower()
    if command_name not in {"command", "cmd"}:
        return

    if not isinstance(message.author, discord.Member):
        return

    if not Permission.can_manage(message.author, cfg):
        await message.reply(
            "You do not have permission to run ER:LC commands.",
            delete_after=5,
        )
        return

    if not parts:
        await message.reply(
            f"Usage: {cfg.prefix}command <command> <text>\\n"
            f"Examples: {cfg.prefix}command hint Hello! | "
            f"{cfg.prefix}command kick Player Reason",
            delete_after=10,
        )
        return

    erlc_command = parts.pop(0).lower()

    aliases = {
        "hint": "h",
        "message": "m",
        "msg": "m",
        "kick": "kick",
        "ban": "ban",
        "unban": "unban",
        "warn": "warn",
        "kill": "kill",
        "respawn": "respawn",
        "refresh": "refresh",
        "heal": "heal",
        "mod": "mod",
        "admin": "admin",
        "team": "team",
        "tp": "tp",
    }
    erlc_command = aliases.get(erlc_command, erlc_command)

    if not erlc_command.startswith(":"):
        erlc_command = ":" + erlc_command

    rest = " ".join(parts)
    full_command = f"{erlc_command} {rest}".strip()

    try:
        client, _ = await bot.get_erlc(message.guild.id)
        await client.command(full_command)

        try:
            await message.delete()
        except (discord.Forbidden, discord.NotFound):
            pass

        await bot.log_action(
            cfg,
            message.guild,
            "ER:LC Prefix Command",
            (
                f"Staff: {message.author.mention}\\n"
                f"Command: {full_command}"
            ),
        )

    except Exception as exc:
        await message.reply(
            f"Command failed: {exc}",
            delete_after=7,
        )


@bot.event
async def on_message(message):
    await process_prefix(message)
    await bot.process_commands(message)


@tasks.loop(seconds=3)
async def detection_loop():
    await bot.wait_until_ready()

    for guild in bot.guilds:
        try:
            cfg = await get_config(guild.id)

            if (
                not cfg.detection_enabled
                or not cfg.erlc_server_key
            ):
                continue

            client = ERLCClient(
                cfg.erlc_server_key,
                bot.http_session,
            )

            kill_logs = await client.kill_logs()
            players = await client.players()

            positions = {}
            names = {}

            for player in players:
                pid = player_id(player)

                if pid is None:
                    continue

                positions[pid] = player_position(player)
                names[pid] = player_name(player)

            now = time.time()
            new_kills = []

            for item in kill_logs:
                killer = str(
                    item.get(
                        "Killer",
                        item.get("killer", ""),
                    )
                )

                victim = str(
                    item.get(
                        "Killed",
                        item.get("killed", ""),
                    )
                )

                timestamp = float(
                    item.get(
                        "Timestamp",
                        item.get("timestamp", now),
                    )
                )

                key = (
                    killer,
                    victim,
                    timestamp,
                )

                if key in bot.kill_seen:
                    continue

                bot.kill_seen.add(key)

                if (
                    now - timestamp
                    <= max(
                        15,
                        cfg.detection_window + 5,
                    )
                ):
                    new_kills.append(
                        (
                            killer,
                            victim,
                            timestamp,
                        )
                    )

            if len(bot.kill_seen) > 5000:
                bot.kill_seen = set(
                    list(bot.kill_seen)[-2500:]
                )

            for killer_name, victim_name, timestamp in new_kills:
                killer_id = None
                victim_id = None

                for pid, name in names.items():
                    if name.lower() == killer_name.lower():
                        killer_id = pid

                    if name.lower() == victim_name.lower():
                        victim_id = pid

                key = killer_name.lower()
                kill_queue = bot.kill_times[key]
                kill_queue.append(timestamp)

                while (
                    kill_queue
                    and timestamp - kill_queue[0]
                    > cfg.detection_window
                ):
                    kill_queue.popleft()

                if len(kill_queue) < cfg.detection_kills:
                    continue

                live_distance = None

                if (
                    killer_id is not None
                    and victim_id is not None
                ):
                    live_distance = distance(
                        positions.get(killer_id),
                        positions.get(victim_id),
                    )

                if (
                    live_distance is None
                    or live_distance
                    < cfg.detection_distance
                ):
                    continue

                cooldown_key = (
                    guild.id,
                    key,
                )

                last_detection = bot.detection_cooldowns.get(
                    cooldown_key,
                    0,
                )

                if (
                    now - last_detection
                    < cfg.detection_cooldown
                ):
                    continue

                bot.detection_cooldowns[
                    cooldown_key
                ] = now

                reason = (
                    "Possible high-distance kill pattern: "
                    f"{len(kill_queue)} kills in "
                    f"{cfg.detection_window:g}s; "
                    f"latest live distance approximately "
                    f"{live_distance:.1f} studs."
                )

                color = (
                    discord.Color.red()
                    if cfg.detection_action
                    in {"kick", "ban"}
                    else discord.Color.orange()
                )

                await bot.log_action(
                    cfg,
                    guild,
                    "ER:LC Cheater Detection",
                    (
                        f"Player: {killer_name}\n"
                        f"Configured action: "
                        f"{cfg.detection_action}\n"
                        f"Details: {reason}"
                    ),
                    color,
                )

                if cfg.detection_action in {
                    "kick",
                    "ban",
                }:
                    punishment = (
                        f":{cfg.detection_action} "
                        f"{killer_name} {reason}"
                    )

                    try:
                        await client.command(punishment)
                    except Exception:
                        log.exception(
                            "Automatic punishment failed for %s",
                            killer_name,
                        )

        except Exception:
            log.exception(
                "Detection loop failed for guild %s",
                guild.id,
            )


@detection_loop.before_loop
async def before_detection():
    await bot.wait_until_ready()


@bot.event
async def on_ready():
    log.info(
        "Logged in as %s (%s) in %s guilds",
        bot.user,
        bot.user.id,
        len(bot.guilds),
    )


@bot.tree.error
async def on_app_command_error(
    interaction,
    error,
):
    if isinstance(
        error,
        app_commands.CheckFailure,
    ):
        message = (
            str(error)
            or "You do not have permission to use this command."
        )

        if interaction.response.is_done():
            await interaction.followup.send(
                message,
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                message,
                ephemeral=True,
            )

        return

    log.exception(
        "Slash command error",
        exc_info=error,
    )

    if interaction.response.is_done():
        await interaction.followup.send(
            "Something went wrong while running that command.",
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(
            "Something went wrong while running that command.",
            ephemeral=True,
        )


token = os.getenv("DISCORD_TOKEN")

if not token:
    raise RuntimeError(
        "DISCORD_TOKEN is missing from .env/environment."
    )

bot.run(token)
