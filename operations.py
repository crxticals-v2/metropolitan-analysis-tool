"""
cogs/operations.py

Metropolitan Unit administrative & operational commands:
  - /metro_log_training
  - /metro_promote
  - /metro_announcement
  - /metro_infract
  - /metro_mass_shift
  - /host_metro_training
  - /metro_openings
  - /request_metro
  - /k9_deploy
"""

import json
import time
import datetime
import discord
from discord import app_commands
import random
from discord.ext import commands
import asyncio
from pathlib import Path

import aiohttp

from liveops import LiveOpAssignmentView, _embed_setup

OWNER_UID = 613698960133062687
BASE_DIR = Path(__file__).parent.resolve()
METRO_ICON_URL = "https://i.imgur.com/qdvbBqe.png"
METRO_EMOJI = "<:LAPD_Metropolitan:1495867271501975552>"
DASHBOARD_DIVIDER = "<:line:1500739607568842865>" * 16

CHANNEL_LABELS = {
    "metro_openings": "Openings",
    "metro_handbook": "Handbook Auto-Send",
    "watchlist_auto": "Watchlist Auto-Send",
    "intelligence_command": "Intelligence Command Channel",
    "metro_promote": "Promotions",
    "metro_infract": "Infractions",
    "metro_announcement": "Announcements",
    "metro_mass_shift": "Mass Shifts",
    "request_metro": "Metro Requests",
    "host_metro_training": "Training Host",
    "metro_log_training": "Training Results",
    "after_action": "After Action Reports",
    "k9": "K9 Logs",
    "archives": "Archives",
    "metro_shop": "Shop",
    "metro_cases": "Major Crimes",
    "raffle_winner": "Raffle Winner Announcement",
    "live_ops_readiness": "Live Operations Readiness",
    "aar_screening_alerts": "AAR Screening Alerts",
    "training_tracker": "Training Tracker",
}

COMMAND_LABELS = {
    "metro_predict": "/metro_predict",
    "metro_suspect_log": "/metro_suspect_log",
    "metro_promote": "/metro_promote",
    "metro_infract": "/metro_infract",
    "metro_mass_shift": "/metro_mass_shift",
    "request_metro": "/request_metro",
    "metro_rapid_aar": "-metroAA (Rapid AAR)",
}


def _channel_label(key: str) -> str:
    return CHANNEL_LABELS.get(key, key.replace("_", " ").title())


def _command_label(key: str) -> str:
    return COMMAND_LABELS.get(key, key)


def _dashboard_embed(cog) -> discord.Embed:
    channels = cog.config_cache.get("channels", {})
    permissions = cog.config_cache.get("permissions", {})

    embed = discord.Embed(
        description=(
            f"## {METRO_EMOJI} | S.I.M.O.N. Command & Control\n"
            f"{DASHBOARD_DIVIDER}\n"
            "Configure Metropolitan routing, command access, and organised-crime intelligence from one control surface.\n\n"
            f"**Configured Channels:** `{len(channels)}`\n"
            f"**Restricted Commands:** `{len(permissions)}`\n"
            f"**Route Coverage:** `{len(channels)}/{len(CHANNEL_LABELS)}` systems\n\n"
            "Select a module below to continue."
        ),
        color=discord.Color.from_rgb(36, 99, 235),
    )
    embed.set_thumbnail(url=METRO_ICON_URL)
    embed.set_footer(text="Owner-only administrative dashboard")
    return embed


def _dashboard_panel_embed(title: str, body: str, color: discord.Color) -> discord.Embed:
    embed = discord.Embed(
        description=f"## {title}\n{DASHBOARD_DIVIDER}\n{body}",
        color=color,
    )
    embed.set_thumbnail(url=METRO_ICON_URL)
    embed.set_footer(text="S.I.M.O.N. Command & Control")
    return embed

# ──────────────────────────────────────────────
# VEHICLE DATABASE SYSTEM
# ──────────────────────────────────────────────

VEHICLE_DB_PATH = BASE_DIR / "erlc_vehicles.json"
if VEHICLE_DB_PATH.exists():
    with open(VEHICLE_DB_PATH, "r") as f:
        VEHICLE_DB = json.load(f)["vehicles"]

# Pre-calculate search blobs and labels for efficiency
VEHICLE_LOOKUP = {}
VEHICLE_SEARCH_LIST = []
for v in VEHICLE_DB:
    label = f"{v.get('brand','')} {v.get('model','')}/{v.get('real_name','')}"
    VEHICLE_LOOKUP[label] = v
    VEHICLE_SEARCH_LIST.append({
        "label": label,
        "search": f"{v.get('brand','')} {v.get('based_on', '')} {v.get('model','')} {v.get('real_name','')}".lower()
    })



# ──────────────────────────────────────────────
# DASHBOARD UI COMPONENTS
# ──────────────────────────────────────────────

class GangMOModal(discord.ui.Modal):
    mo = discord.ui.TextInput(label="Modus Operandi / Intelligence", style=discord.TextStyle.paragraph, placeholder="Paragraph describing base of ops, pathing, behavior...", required=True)
    vehicles = discord.ui.TextInput(label="Preferred Vehicles", placeholder="e.g. Black Challengers, SUVs...", required=True)
    clothing = discord.ui.TextInput(label="Uniform / Clothing Description", placeholder="e.g. Green bandanas, tactical vests...", required=True)

    def __init__(self, cog, gang_shorthand: str):
        super().__init__(title=f"Configure Intelligence: {gang_shorthand}")
        self.cog = cog
        self.shorthand = gang_shorthand

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.settings.update_one(
            {"_id": f"gang_{self.shorthand}"},
            {"$set": {
                "mo": self.mo.value,
                "vehicles": self.vehicles.value,
                "clothing": self.clothing.value
            }},
            upsert=True
        )
        await interaction.response.send_message(f"✅ Intelligence updated for **{self.shorthand}**.", ephemeral=True)


class GangConfigView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=300)
        self.cog = cog

    @discord.ui.select(
        placeholder="Select an organised-crime profile...",
        options=[
            discord.SelectOption(label="77th Saints Gang", description="Update M.O., vehicles, and clothing identifiers", value="77th", emoji="🟡"),
            discord.SelectOption(label="West Coast Cartel", description="Update cartel behaviour and vehicle intelligence", value="WCC", emoji="🔵"),
            discord.SelectOption(label="Noche Silente Hermanos", description="Update NSH profile and field identifiers", value="NSH", emoji="⚫"),
        ]
    )
    async def gang_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        await interaction.response.send_modal(GangMOModal(self.cog, select.values[0]))


class DashboardView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=300)
        self.cog = cog
        self.mode = None
        self.selected_command = None

    @discord.ui.select(
        placeholder="Select a dashboard module...",
        options=[
            discord.SelectOption(
                label="Channel Routing",
                description="Choose where operational outputs and logs are posted",
                value="channels",
                emoji="📡",
            ),
            discord.SelectOption(
                label="Command Permissions",
                description="Restrict sensitive commands to selected roles",
                value="perms",
                emoji="🛡️",
            ),
            discord.SelectOption(
                label="Gang Intelligence",
                description="Maintain M.O., vehicle, and clothing records",
                value="gangs",
                emoji="🏙️",
            ),
        ]
    )
    async def mode_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.mode = select.values[0]

        if self.mode == "channels":
            await interaction.response.edit_message(
                content=None,
                embed=_dashboard_panel_embed(
                    "📡 | Channel Routing",
                    "Select a Metropolitan system, then choose the Discord channel or forum where its output should land.",
                    discord.Color.from_rgb(56, 189, 248),
                ),
                view=ChannelView(self.cog)
            )
        elif self.mode == "perms":
            await interaction.response.edit_message(
                content=None,
                embed=_dashboard_panel_embed(
                    "🛡️ | Command Permissions",
                    "Select a command, then choose the roles allowed to use it. Empty permission sets keep the default behaviour.",
                    discord.Color.from_rgb(99, 102, 241),
                ),
                view=PermissionView(self.cog)
            )
        else:
            await interaction.response.edit_message(
                content=None,
                embed=_dashboard_panel_embed(
                    "🏙️ | Gang Intelligence",
                    "Select an organised-crime profile to update its M.O., preferred vehicles, and clothing identifiers.",
                    discord.Color.from_rgb(202, 138, 4),
                ),
                view=GangConfigView(self.cog)
            )


class ChannelView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=300)
        self.cog = cog
        self.key = None

    @discord.ui.select(
        placeholder="Select a system to route...",
        options=[
            discord.SelectOption(label="Openings", description="Rank availability and roster updates", value="metro_openings", emoji="📋"),
            discord.SelectOption(label="Handbook Auto-Send", description="Interactive handbook menu destination", value="metro_handbook", emoji="📘"),
            discord.SelectOption(label="Watchlist Auto-Send", description="Automated suspect and gang intelligence board", value="watchlist_auto", emoji="🛰️"),
            discord.SelectOption(label="Intel Command Channel", description="Where SIMON intelligence commands are allowed", value="intelligence_command", emoji="🧠"),
            discord.SelectOption(label="Promotions", description="Promotion announcements and logs", value="metro_promote", emoji="⬆️"),
            discord.SelectOption(label="Infractions", description="Disciplinary notices", value="metro_infract", emoji="⚠️"),
            discord.SelectOption(label="Announcements", description="Division-wide announcements", value="metro_announcement", emoji="📣"),
            discord.SelectOption(label="Mass Shifts", description="Mobilisation alerts", value="metro_mass_shift", emoji="🚨"),
            discord.SelectOption(label="Metro Requests", description="Cross-division backup requests", value="request_metro", emoji="📞"),
            discord.SelectOption(label="Training Host", description="Training session posts", value="host_metro_training", emoji="🎓"),
            discord.SelectOption(label="Training Results", description="Evaluation results and score logs", value="metro_log_training", emoji="📝"),
            discord.SelectOption(label="After Action", description="AAR routing", value="after_action", emoji="📄"),
            discord.SelectOption(label="K9 Logs", description="K-Platoon deployment logs", value="k9", emoji="🐕"),
            discord.SelectOption(label="Archives", description="Weekly archive summaries", value="archives", emoji="🗄️"),
            discord.SelectOption(label="Shop", description="Intel point reward requests", value="metro_shop", emoji="🛒"),
            discord.SelectOption(label="Major Crimes", description="Case forum/thread routing", value="metro_cases", emoji="🧾"),
            discord.SelectOption(label="Raffle Winner", description="Weekly raffle winner announcements", value="raffle_winner", emoji="🎟️"),
            discord.SelectOption(label="Live Ops Readiness", description="Operational readiness boards", value="live_ops_readiness", emoji="📡"),
            discord.SelectOption(label="AAR Screening Alerts", description="Inappropriate rapid AAR detections", value="aar_screening_alerts", emoji="🚫"),
            discord.SelectOption(label="Training Tracker", description="Live trainee availability counter (auto-refreshes every 24h)",value="training_tracker",emoji="🎓"),
        ]
    )
    async def feature_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.key = select.values[0]
        await interaction.response.send_message(
            embed=_dashboard_panel_embed(
                "📍 | Select Destination",
                f"Choose the destination for **{_channel_label(self.key)}**.",
                discord.Color.from_rgb(56, 189, 248),
            ),
            view=ChannelPicker(self.cog, self.key),
            ephemeral=True
        )


class ChannelPicker(discord.ui.View):
    def __init__(self, cog, key):
        super().__init__(timeout=120)
        self.cog = cog
        self.key = key

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text, discord.ChannelType.forum],
    )
    async def pick_channel(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        channel = select.values[0]

        if self.key == "metro_handbook" and isinstance(channel, discord.ForumChannel):
            await interaction.response.send_message(
                embed=_dashboard_panel_embed(
                    "⚠️ | Text Channel Required",
                    "The handbook is a live component message, so it must be routed to a normal text channel.",
                    discord.Color.orange(),
                ),
                ephemeral=True,
            )
            return

        await self.cog.bot.mongo_client["erlc_database"]["settings"].update_one(
            {"_id": "guild_config"},
            {"$set": {f"channels.{self.key}": channel.id}},
            upsert=True
        )

        await self.cog.load_config()

        await interaction.response.send_message(
            embed=_dashboard_panel_embed(
                "✅ | Channel Route Updated",
                f"**{_channel_label(self.key)}** will now post to {channel.mention}.",
                discord.Color.green(),
            ),
            ephemeral=True
        )
        if self.key == "training_tracker":
            ops = self.cog.bot.get_cog("Operations")
            if ops:
                try:
                    await ops._post_training_tracker(channel.id)
                    await interaction.followup.send(
                        "✅ Training Tracker posted and will refresh every 24 hours.",
                        ephemeral=True,
                    )
                except Exception as exc:
                    await interaction.followup.send(
                        f"⚠️ Route saved, but the tracker could not be auto-sent: `{exc}`",
                        ephemeral=True,
                    )

        if self.key == "metro_handbook":
            handbook = self.cog.bot.get_cog("Handbook")
            if handbook:
                try:
                    await handbook.publish_to_channel(channel)
                    await interaction.followup.send(
                        "✅ Handbook menu auto-sent to the configured channel.",
                        ephemeral=True,
                    )
                except Exception as exc:
                    await interaction.followup.send(
                        f"⚠️ Route saved, but the handbook could not be auto-sent: `{exc}`",
                        ephemeral=True,
                    )


class PermissionView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=300)
        self.cog = cog
        self.cmd = None

    @discord.ui.select(
        placeholder="Select a command to restrict...",
        options=[
            discord.SelectOption(label="/metro_predict", description="SIMON route prediction", value="metro_predict", emoji="🧭"),
            discord.SelectOption(label="/metro_suspect_log", description="Suspect intelligence logging", value="metro_suspect_log", emoji="🧠"),
            discord.SelectOption(label="/metro_promote", description="Promotion command", value="metro_promote", emoji="⬆️"),
            discord.SelectOption(label="/metro_infract", description="Disciplinary command", value="metro_infract", emoji="⚠️"),
            discord.SelectOption(label="/metro_mass_shift", description="Division mobilisation", value="metro_mass_shift", emoji="🚨"),
            discord.SelectOption(label="/request_metro", description="Cross-division backup request", value="request_metro", emoji="📞"),
            discord.SelectOption(label="-metroAA (Rapid AAR)", description="Natural-language AAR parser", value="metro_rapid_aar", emoji="📄"),
        ]
    )
    async def command_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.cmd = select.values[0]
        await interaction.response.send_message(
            embed=_dashboard_panel_embed(
                "🔐 | Select Allowed Roles",
                f"Choose roles allowed to use **{_command_label(self.cmd)}**.",
                discord.Color.from_rgb(99, 102, 241),
            ),
            view=RolePicker(self.cog, self.cmd),
            ephemeral=True
        )


class RolePicker(discord.ui.View):
    def __init__(self, cog, cmd):
        super().__init__(timeout=120)
        self.cog = cog
        self.cmd = cmd

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        min_values=1,
        max_values=10
    )
    async def pick_roles(self, interaction: discord.Interaction, select: discord.ui.RoleSelect):
        roles = [r.id for r in select.values]

        await self.cog.bot.mongo_client["erlc_database"]["settings"].update_one(
            {"_id": "guild_config"},
            {"$set": {f"permissions.{self.cmd}": roles}},
            upsert=True
        )

        await self.cog.load_config()

        await interaction.response.send_message(
            embed=_dashboard_panel_embed(
                "✅ | Permissions Updated",
                f"**{_command_label(self.cmd)}** is now restricted to `{len(roles)}` selected role{'s' if len(roles) != 1 else ''}.",
                discord.Color.green(),
            ),
            ephemeral=True
        )

# ──────────────────────────────────────────────
# MODALS
# ──────────────────────────────────────────────

async def _send_training_embed(interaction: discord.Interaction, embed: discord.Embed):
    operations_cog = interaction.client.get_cog("Operations") if interaction.client else None
    if operations_cog:
        target = await operations_cog._resolve_output_channel(interaction, "metro_log_training")
    else:
        target = interaction.channel
    await target.send(embed=embed)


class MetroTrainingModal(discord.ui.Modal):
    """Score entry modal for a Metro entry training session."""

    s1 = discord.ui.TextInput(
        label="SECT.I – Firearms Exercise",
        placeholder="Score (0-10)",
        min_length=1,
        max_length=2,
    )
    s2 = discord.ui.TextInput(
        label="SECT.II – Stealth/Tactical Exercise",
        placeholder="Score (0-10)",
        min_length=1,
        max_length=2,
    )
    s3 = discord.ui.TextInput(
        label="SECT.III – Specialist Protection",
        placeholder="Score (0-10)",
        min_length=1,
        max_length=2,
    )

    def __init__(self, host, co_host, trainee, outcome, notes):
        super().__init__(title="Metro Entry Training Score Entry")
        self.host    = host
        self.co_host = co_host
        self.trainee = trainee
        self.outcome = outcome
        self.notes   = notes

    async def on_submit(self, interaction: discord.Interaction):
        try:
            score1 = int(self.s1.value)
            score2 = int(self.s2.value)
            score3 = int(self.s3.value)
            total  = score1 + score2 + score3
        except ValueError:
            await interaction.response.send_message(
                "❌ Scores must be valid numbers.", ephemeral=True
            )
            return

        desc = (
            "## <:LAPD_Metropolitan:1495867271501975552> | Training Evaluation\n"
            f"{DASHBOARD_DIVIDER}\n"
            f"**Trainee:** {self.trainee.mention}\n\n"
            f"**Field Training Officer:** {self.host.mention}\n\n"
            f"**Co-Host:** {self.co_host.mention if self.co_host else 'None'}\n\n"
            "### Performance Metrics\n"
            f"**SECT. I | Firearms Exercise:** {score1}/10\n"
            f"**SECT. II | Stealth/Tactical:** {score2}/10\n"
            f"**SECT. III | Specialist Protection:** {score3}/10\n"
            f"**Overall Score:** {total}/30\n"
            f"**Outcome:** {self.outcome}\n\n"
            f"{DASHBOARD_DIVIDER}\n\n"
            "**Notes:**\n"
            f"> {self.notes}\n\n"
            "**What's Next?**\n"
            "If you passed, congratulations! You are now one of us! You will be roled "
            "shortly and get access to the full Unit resources.\n"
            "If you failed, do not be discouraged. You may request training anytime.\n"
        )

        embed = discord.Embed(description=desc, color=discord.Color.blue())
        embed.set_thumbnail(url="https://i.imgur.com/qdvbBqe.png")
        embed.set_footer(
            text=f"Issued by {self.host.display_name}",
            icon_url=self.host.display_avatar.url,
        )

        await _send_training_embed(interaction, embed)
        await interaction.response.send_message(
            "✅ Training log has been posted successfully.", ephemeral=True
        )


class K9TrainingModal(discord.ui.Modal):
    """Score entry modal for a K9 training session."""

    s1 = discord.ui.TextInput(
        label="SCENARIO I – Traffic Stop",
        placeholder="Score (0-10)",
        min_length=1,
        max_length=2,
    )
    s2 = discord.ui.TextInput(
        label="SCENARIO II – Searches",
        placeholder="Score (0-10)",
        min_length=1,
        max_length=2,
    )
    s3 = discord.ui.TextInput(
        label="SCENARIO III – Active Robbery",
        placeholder="Score (0-10)",
        min_length=1,
        max_length=2,
    )

    def __init__(self, host, co_host, trainee, outcome, notes):
        super().__init__(title="K9 Training Score Entry")
        self.host    = host
        self.co_host = co_host
        self.trainee = trainee
        self.outcome = outcome
        self.notes   = notes

    async def on_submit(self, interaction: discord.Interaction):
        try:
            traffic_stop = int(self.s1.value)
            searches = int(self.s2.value)
            active_robbery = int(self.s3.value)
            total = traffic_stop + searches + active_robbery
        except ValueError:
            await interaction.response.send_message(
                "❌ Scores must be valid numbers.", ephemeral=True
            )
            return

        desc = (
            "## <:LAPD_Metropolitan:1495867271501975552> | K9 Training Evaluation\n"
            f"{DASHBOARD_DIVIDER}\n"
            f"**Trainee:** {self.trainee.mention}\n\n"
            f"**K9 Training Officer:** {self.host.mention}\n\n"
            f"**Co-Host:** {self.co_host.mention if self.co_host else 'None'}\n\n"
            "### Scenario Performance\n"
            f"**SCENARIO I | Traffic Stop:** {traffic_stop}/10\n"
            f"**SCENARIO II | Searches:** {searches}/10\n"
            f"**SCENARIO III | Active Robbery:** {active_robbery}/10\n"
            f"**Overall Score:** {total}/30\n"
            f"**Outcome:** {self.outcome}\n\n"
            f"{DASHBOARD_DIVIDER}\n\n"
            "**Notes:**\n"
            f"> {self.notes or 'No additional notes provided.'}\n\n"
            "**What's Next?**\n"
            "If you passed, your K9 training record has been logged for command review.\n"
            "If you failed, review the marked scenarios before requesting another evaluation.\n"
        )

        embed = discord.Embed(description=desc, color=discord.Color.from_rgb(34, 197, 94))
        embed.set_thumbnail(url=METRO_ICON_URL)
        embed.set_footer(
            text=f"Issued by {self.host.display_name}",
            icon_url=self.host.display_avatar.url,
        )

        await _send_training_embed(interaction, embed)
        await interaction.response.send_message(
            "✅ K9 training log has been posted successfully.", ephemeral=True
        )


class MetroAnnouncementModal(discord.ui.Modal):
    """Free-text announcement modal with optional role ping."""

    announcement = discord.ui.TextInput(
        label="Announcement",
        style=discord.TextStyle.paragraph,
        placeholder="Write your announcement here using markdown…",
        required=True,
        max_length=4000,
    )

    def __init__(self, ping_role: bool, role):
        super().__init__(title="Metro Announcement")
        self.ping_role = ping_role
        self.role      = role

    async def on_submit(self, interaction: discord.Interaction):
        content = self.role.mention if self.ping_role and self.role else None

        embed = discord.Embed(
            title="<:LAPD_Metropolitan:1495867271501975552> | Division Announcement",
            description=f"{DASHBOARD_DIVIDER}\n\n{self.announcement.value}\n\n{DASHBOARD_DIVIDER}",
            color=discord.Color.blue(),
        )
        embed.set_footer(
            text=f"Issued by {interaction.user.display_name}",
            icon_url=(
                interaction.user.display_avatar.url
                if interaction.user.display_avatar
                else None
            ),
        )

        target = await interaction.client.get_cog("Operations")._resolve_output_channel(interaction, "metro_announcement")
        await target.send(content=content, embed=embed)
        await interaction.response.send_message(
            "✅ Announcement sent.", ephemeral=True
        )


class IntelHistoryView(discord.ui.View):
    """Paginated view for an officer's intel point history."""
    def __init__(self, history: list, member: discord.Member):
        super().__init__(timeout=180)
        self.history = sorted(history, key=lambda x: x['timestamp'], reverse=True)
        self.member = member
        self.page = 0
        self.per_page = 10

    def make_embed(self):
        start = self.page * self.per_page
        end = start + self.per_page
        chunk = self.history[start:end]
        
        desc = f"## <:LAPD_Metropolitan:1495867271501975552> | Intel Audit: {self.member.display_name}\n"
        desc += f"Showing entries {start+1}–{min(end, len(self.history))} of {len(self.history)}\n"
        desc += f"{DASHBOARD_DIVIDER}\n"
        
        for item in chunk:
            ts = item['timestamp']
            time_str = ts.strftime("%Y-%m-%d %H:%M") if hasattr(ts, 'strftime') else str(ts)

            # Display both Weekly Score and Career Tokens in history
            w_gain = item.get('weekly_gain', item.get('points', 0))
            t_gain = item.get('token_gain', item.get('points', 0) // 2)
            
            prefix = "+" if w_gain >= 0 else ""
            desc += f"`{time_str}` | **{prefix}{w_gain} Score / {prefix}{t_gain} Tokens** | {item['reason']}\n"
            
        embed = discord.Embed(description=desc, color=discord.Color.blue())
        embed.set_footer(text=f"Page {self.page + 1}")
        return embed

    @discord.ui.button(label="Back", style=discord.ButtonStyle.gray)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        await interaction.response.edit_message(embed=self.make_embed(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.gray)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if (self.page + 1) * self.per_page < len(self.history):
            self.page += 1
        await interaction.response.edit_message(embed=self.make_embed(), view=self)


class ShopApprovalView(discord.ui.View):
    """View for High Command to approve or deny shop purchases."""
    def __init__(self, cog, target_user: discord.Member, item_label: str, cost: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.target_user = target_user
        self.item_label = item_label
        self.cost = cost
        self._lock = asyncio.Lock()
        self._handled = False

    @discord.ui.button(label="Approve Purchase", style=discord.ButtonStyle.success, emoji="✅")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.cog._is_senior_high_command(interaction.user):
            return await interaction.response.send_message("❌ Only Senior High Command can approve shop requests.", ephemeral=True)

        async with self._lock:
            if self._handled:
                return await interaction.response.send_message("❌ This request has already been processed.", ephemeral=True)
            
            self._handled = True

        # Double check Token balance before deduction
        data = await self.cog.officer_stats.find_one({"_id": self.target_user.id})
        current_points = data.get("intel_points", 0) if data else 0

        if current_points < self.cost:
            return await interaction.response.send_message(f"❌ {self.target_user.display_name} no longer has enough Career Tokens.", ephemeral=True)

        # Deduct Career Tokens
        update_data = {
            "$inc": {"intel_points": -self.cost},
            "$push": {"history": {
                "reason": f"Shop Purchase: {self.item_label}", 
                "weekly_gain": 0,
                "token_gain": -self.cost,
                "timestamp": datetime.datetime.now(datetime.timezone.utc)
            }}
        }

        # Handle 24hr Multiplier specific logic
        if self.item_label == "24hr Point Multiplier (1.5x)":
            expiry = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=24)
            update_data["$set"] = {"multiplier_expiry": expiry}

        await self.cog.officer_stats.update_one({"_id": self.target_user.id}, update_data)

        # Update Embed
        embed = interaction.message.embeds[0]
        embed.title = None
        embed.color = discord.Color.green()
        embed.set_field_at(0, name="Status", value="✅ Approved & Tokens Deducted", inline=False)
        embed.add_field(name="Approved By", value=interaction.user.mention, inline=False)
        
        await interaction.message.edit(embed=embed, view=None)
        await interaction.message.create_thread(name=f"Fulfillment: {self.target_user.display_name}")
        
        await interaction.response.send_message(f"✅ Points deducted and thread created for {self.target_user.mention}.", ephemeral=True)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, emoji="✖️")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.cog._is_high_command(interaction.user):
            return await interaction.response.send_message("❌ Only High Command can deny shop requests.", ephemeral=True)

        async with self._lock:
            if self._handled:
                return await interaction.response.send_message("❌ This request has already been processed.", ephemeral=True)
            
            self._handled = True

        embed = interaction.message.embeds[0]
        embed.title = None
        embed.color = discord.Color.red()
        embed.set_field_at(0, name="Status", value="❌ Denied", inline=False)
        await interaction.message.edit(embed=embed, view=None)
        await interaction.response.send_message("Purchase request denied.", ephemeral=True)


class ShopView(discord.ui.View):
    """Dropdown menu for officers to select items."""
    def __init__(self, cog, current_points: int):
        super().__init__(timeout=60)
        self.cog = cog
        self.points = current_points
        self.items = {
            "shift_15":   {"label": "Shift Time (+15 minutes)", "cost": 3},
            "shift_30":   {"label": "Shift Time (+30 minutes)", "cost": 6},
            "hint":       {"label": "Hint on next Drill/Hunt", "cost": 5},
            "quota":      {"label": "Quota Exemption (1 Week)", "cost": 12},
            "ic_shift":   {"label": "1 Shift as Responding Incident Commander", "cost": 30},
            "multiplier": {"label": "24hr Point Multiplier (1.5x)", "cost": 20},
            "raffle_1":   {"label": "Raffle Ticket (×1)", "cost": 3},
            "raffle_2":   {"label": "Raffle Tickets (×2)", "cost": 5},
        }

    @discord.ui.select(
        placeholder="Select an item to redeem...",
        options=[
            discord.SelectOption(label="Shift Time (+15m)", description="Cost: 3 Tokens", value="shift_15", emoji="⏰"),
            discord.SelectOption(label="Shift Time (+30m)", description="Cost: 6 Tokens", value="shift_30", emoji="⏰"),
            discord.SelectOption(label="Next Drill/Hunt Hint", description="Cost: 5 Tokens", value="hint", emoji="📄"),
            discord.SelectOption(label="Quota Exemption (1 Week)", description="Cost: 12 Tokens", value="quota", emoji="💆"),
            discord.SelectOption(label="Shift as Responding IC", description="Cost: 30 Tokens", value="ic_shift", emoji="🫡"),
            discord.SelectOption(label="24hr 1.5x Multiplier", description="Cost: 20 Tokens", value="multiplier", emoji="✖️"),
            discord.SelectOption(label="View My Raffle Tickets", description="Check your weekly entries and draw odds", value="raffle_status", emoji="🏆"),
            discord.SelectOption(label="Raffle Ticket ×1", description="3 Tokens • weekly 50 Robux draw", value="raffle_1", emoji="🎟️"),
            discord.SelectOption(label="Raffle Tickets ×2", description="5 Tokens • best raffle value", value="raffle_2", emoji="🎟️"),
        ]
    )
    async def select_item(self, interaction: discord.Interaction, select: discord.ui.Select):
        # ── Raffle ticket fast-path (no HC approval needed) ──────────────
        if select.values[0] in ("raffle_status", "raffle_1", "raffle_2"):
            raffle_cog = interaction.client.get_cog("RaffleCog")
            if raffle_cog:
                if select.values[0] == "raffle_status":
                    await raffle_cog.show_ticket_status(interaction, edit_message=True)
                else:
                    await raffle_cog.purchase_tickets(interaction, self, select.values[0])
            else:
                await interaction.response.send_message("Raffle system is currently unavailable.", ephemeral=True)
            return
        # ─────────────────────────────────────────────────────────────────

        item = self.items[select.values[0]]

        shop_channel = await self.cog._resolve_output_channel(interaction, "metro_shop")
        
        # Target pings for both CO and DCO
        target_roles = ["[𝐌𝐄𝐓] Commanding Officer", "[𝐌𝐄𝐓] Deputy Commanding Officer"]
        ping_mentions = [
            r.mention for r in interaction.guild.roles 
            if any(name == r.name for name in target_roles)
        ]

        embed = discord.Embed(
            description=(
                "## 🎉 | Metro Shop Rewards\n"
                f"{DASHBOARD_DIVIDER}\n"
                f"**Officer:** {interaction.user.mention}\n"
                f"**Item:** {item['label']}\n"
                f"**Cost:** `{item['cost']}` Career Tokens\n"
                f"**Token Balance Before:** `{self.points}`\n"
                f"**Token Balance After:** `{self.points - item['cost']}`\n"
            ),
            color=discord.Color.gold()
        )
        embed.add_field(name="Status", value="⏳ Awaiting Approval", inline=False)

        await shop_channel.send(content=" ".join(ping_mentions), embed=embed, view=ShopApprovalView(self.cog, interaction.user, item['label'], item['cost']))
        await interaction.response.edit_message(content="✅ Your request has been sent to Senior High Command for approval.", embed=None, view=None)


class WeeklyResetView(discord.ui.View):
    """Confirmation view for purging weekly logs."""
    def __init__(self, cog):
        super().__init__(timeout=60)
        self.cog = cog

    @discord.ui.button(label="Confirm Purge & Reset", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 1. Authority Check (Should happen before any operational logic)
        allowed = ["Chief Inspector", "Detective Chief Inspector", "Deputy Commanding Officer", "Commanding Officer"]
        if not any(role.name in [f"[𝐌𝐄𝐓] {r}" for r in allowed] or r in role.name for r in allowed for role in interaction.user.roles):
            await interaction.response.send_message("❌ You lack the authority to perform a weekly reset.", ephemeral=True)
            return

        # Acknowledge immediately to avoid 3s timeout (Error 10062)
        await interaction.response.defer()

        # 2. Run Raffle (Correctly call the method on the instance)
        raffle_cog = interaction.client.get_cog('RaffleCog')
        if raffle_cog:
            await raffle_cog.run_raffle(interaction.guild, interaction)

        # 1. Resolve Timeframe & Stats
        last_reset = self.cog.config_cache.get("last_reset")
        if isinstance(last_reset, str):
            last_reset = datetime.datetime.fromisoformat(last_reset)
        
        aar_count = await self.cog.aar_logs.count_documents({})
        k9_count = await self.cog.k9_logs.count_documents({})
        case_count = await self.cog.case_logs.count_documents({})
        
        # Filter suspect logs by the current week only
        intel_query = {}
        if last_reset:
            intel_query["timestamp"] = {"$gte": last_reset}
        intel_count = await self.cog.suspect_logs.count_documents(intel_query)
        
        # Get top officer based on weekly_points
        cursor = self.cog.officer_stats.find().sort("weekly_points", -1).limit(1)
        top_officer_data = await cursor.to_list(length=1)
        top_text = "N/A"
        if top_officer_data:
            m = interaction.guild.get_member(top_officer_data[0]["_id"])
            top_text = f"{m.mention} ({top_officer_data[0].get('weekly_points', 0)} pts)" if m else f"ID: {top_officer_data[0]['_id']}"

        archive_embed = discord.Embed(
            title="<:LAPD_Metropolitan:1495867271501975552> Weekly Operations Archive",
            description=(
                f"## **Operational Period:** {datetime.datetime.now().strftime('%Y-%m-%d')}\n"
                f"{DASHBOARD_DIVIDER}\n"
                f"📝 **AARs Processed:** `{aar_count}`\n"
                f"🐕 **K9 Deployments:** `{k9_count}`\n"
                f"🔍 **Suspect Intelligence:** `{intel_count}` logs\n"
                f"📂 **Case Logs:** `{case_count}`\n\n"
                f"🏆 **Weekly Valedictorian:** {top_text}\n"
                "**━━━━━━━━━━━━━━━━━━━━**\n"
            ),
            color=discord.Color.dark_grey()
        )
        
        archive_channel = await self.cog._resolve_output_channel(interaction, "archives")
        if archive_channel:
            await archive_channel.send(embed=archive_embed)

        # Purge K9 logs only.
        # NOTE: AARs, Case Logs, and suspect_logs are preserved for persistent data and AI training.
        await self.cog.k9_logs.delete_many({})
        
        # 3. Update Reset Timestamp & Clear Weekly Counters
        now = datetime.datetime.now(datetime.timezone.utc)
        await self.cog.settings.update_one(
            {"_id": "guild_config"},
            {"$set": {"last_reset": now}},
            upsert=True
        )
        await self.cog.officer_stats.update_many({}, {"$set": {"weekly_points": 0}})
        await self.cog.load_config() # Refresh cache

        embed = discord.Embed(
            title="🧹 Weekly Operations Purged",
            description="K9 Deployments have been cleared. After Action Reports and Case Logs remain persistent. Officer points have been preserved.",
            color=discord.Color.gold()
        )
        await interaction.followup.send(embed=embed)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Reset cancelled.", ephemeral=True)
        self.stop()


# ──────────────────────────────────────────────
# COG
# ──────────────────────────────────────────────
class Operations(commands.Cog):
    """Metropolitan Unit administrative commands."""

    HIGH_COMMAND_RANKS = {"[𝐌𝐄𝐓] Chief Inspector", "[𝐌𝐄𝐓] Detective Chief Inspector", "[𝐌𝐄𝐓] Deputy Commanding Officer", "[𝐌𝐄𝐓] Commanding Officer"}
    SENIOR_HIGH_COMMAND_RANKS = {"[𝐌𝐄𝐓] Deputy Commanding Officer", "[𝐌𝐄𝐓] Commanding Officer"}

    def __init__(self, bot):
        self.bot = bot
        self.user_links = self.bot.mongo_client["erlc_database"]["user_links"]
        self.metro_cases = self.bot.mongo_client["erlc_database"]["metro_cases"]
        self.settings = self.bot.mongo_client["erlc_database"]["settings"]
        
        # New Intelligence Collections
        self.aar_logs = self.bot.mongo_client["erlc_database"]["aar_logs"]
        self.k9_logs = self.bot.mongo_client["erlc_database"]["k9_logs"]
        self.case_logs = self.bot.mongo_client["erlc_database"]["case_logs"]
        self.live_ops = self.bot.mongo_client["erlc_database"]["live_ops"]
        self.officer_stats = self.bot.mongo_client["erlc_database"]["officer_stats"]
        
        # SIMON Intelligence Collection (Persistent)
        self.suspect_logs = self.bot.mongo_client["erlc_database"]["suspect_logs"]
        
        self.config_cache = {}
        self._tracker_task: asyncio.Task | None = None

    async def cog_load(self):
        await self.load_config()
        # Start the 24-hour training tracker refresh loop
        self._tracker_task = asyncio.create_task(self._training_tracker_loop())
    
    async def cog_unload(self):
        if self._tracker_task:
            self._tracker_task.cancel()

    def _normalize_gang_shorthand(self, text: str) -> str | None:
        """Maps full names or shorthands to the principle gang tags."""
        if not text: return None
        t = text.lower()
        if "77th" in t or "saints" in t: return "77th"
        if "wcc" in t or "west coast cartel" in t: return "WCC"
        if "nsh" in t or "noche silente" in t or "hermanos" in t: return "NSH"
        return None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listens for the -metroAA prefix for rapid AAR generation."""
        if message.author.bot or not message.guild or not message.content.startswith("-metroAA"):
            return

        # Permission check using the dashboard configuration
        if not self._check_member_permission(message.author, "metro_rapid_aar"):
            return

        raw_text = message.content[len("-metroAA"):].strip()
        if not raw_text:
            return

        # Start the process
        success = await self._process_quick_aar(message, raw_text)
        
        if success:
            try:
                await message.add_reaction("✅")
                await asyncio.sleep(2) # Brief delay so they see the success
                await message.delete()
            except:
                pass
        else:
            try:
                await message.add_reaction("❌")
            except:
                pass

    async def _process_quick_aar(self, message: discord.Message, text: str) -> bool:
        """Parses raw text via LLM and posts a structured AAR."""
        from llm import call_llm

        # Specialized prompt for AAR extraction
        prompt = f"""
        You are a highly detailed Metropolitan Intelligence Clerk.
        Extract and EXPAND on the After Action Report (AAR) data from this unstructured text into a professional, detailed tactical summary.
        If the officer is being inappropriate, or the situation is obviously not a valid police/crime scenario, flag it as invalid and do NOT attempt to fabricate details. Even if there are details that seems like police action was taken, it is highly possible it is a troller, trolling.
        If it is indeed a troller, you will flag the content as invalid, and your response will be used to ping Senior High Command for review. Do NOT attempt to fabricate any details and do NOT attempt to send the AAR report. You will just flag is at wrong and code logic will deal with them.
        Officer: {message.author.display_name}
        Text: "{text}"

        VALID NODES (NodeID: POI):
        {self.bot.get_cog("Simon")._nodes_prompt_cache if self.bot.get_cog("Simon") else "N-205: Bank"}

        TASK: Map the patrol_area mentioned to the closest valid node ID (N-XXX) from the list above.

        GUIDELINES:
        - officers: (string, default to officer name)
        - suspect_name: (string, the Roblox username if mentioned, else null)
        - patrol_area: (string, e.g. "Industrial", "Downtown")
        - suspicious_activity: (A descriptive, multi-sentence paragraph detailing the specific behaviors, threat indicators, and movements observed. Do not be brief.)
        - actions_taken: (A descriptive, multi-sentence paragraph detailing the unit's tactical response, engagement maneuvers, or enforcement steps. Provide a professional narrative flow.)
        - vehicle: (string, identify the brand and model. Do NOT include colors.)
        - suspect_gender: (string, Male/Female/Unknown)
        - clothing: (string, description)
        - direction_of_travel: (string, e.g. "Northbound", "Towards Bank")
        - node_id: (The N-XXX ID from the provided node list that best matches the patrol_area)
        - gang_affiliation: (string, identify if they belong to 77th, WCC, or NSH)
        - is_valid_incident: (boolean, set to false if the input is inappropriate, offensive, nonsense, or has absolutely no relation to a police/crime scenario)

        Return ONLY JSON (strictly no preambles or postambles) in this format:
        {{
          "prediction": {{
            "officers": "string",
            "suspect_name": "string",
            "patrol_area": "string",
            "suspicious_activity": "string",
            "actions_taken": "string",
            "vehicle": "string",
            "suspect_gender": "string",
            "clothing": "string",
            "direction_of_travel": "string",
            "gang_affiliation": "string",
            "node_id": "string"
          }}
        }}
        """
        
        response = await call_llm(prompt)
        if not response or "prediction" not in response:
            return False

        data = response["prediction"]
        
        # Screening for inappropriate or irrelevant content
        if not data.get("is_valid_incident", True):
            # Resolve High Command roles for the ping (CO, DCO, CI, DCI)
            hc_ranks = self.HIGH_COMMAND_RANKS
            pings = [
                role.mention for role in message.guild.roles 
                if any(rank in role.name for rank in hc_ranks)
            ]
            
            alert_embed = discord.Embed(
                title="⚠️ Insubordination Alert | Rapid AAR Screening",
                description=(
                    f"**Officer:** {message.author.mention} ({message.author.id})\n"
                    f"**Channel:** {message.channel.mention}\n"
                    f"**Timestamp:** <t:{int(time.time())}:F>\n\n"
                    f"**Flagged Content:**\n> {text[:1800]}"
                ),
                color=discord.Color.red()
            )
            
            log_channel = await self._resolve_output_channel(None, "aar_screening_alerts")
            await log_channel.send(content=" ".join(pings), embed=alert_embed)
            return False

        # Vehicle Matching
        extracted_veh = data.get("vehicle", "Unknown")
        final_vehicle = "Unknown Vehicle"
        for v_search in VEHICLE_SEARCH_LIST:
            if extracted_veh.lower() in v_search["search"]:
                final_vehicle = v_search["label"]
                break

        rank = self._get_user_rank(message.author)
        time_display = f"<t:{int(time.time())}:F>"

        secondary_mentions = [m.mention for m in message.mentions if m.id != message.author.id and not m.bot]
        secondary_str = f", {', '.join(secondary_mentions)}" if secondary_mentions else ""

        desc = (
            "## <:LAPD_Metropolitan:1495867271501975552> | Rapid After Action Report\n"
            "**━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━**\n\n"
            f"**Officer(s):** {data.get('officers', message.author.display_name)}{secondary_str}\n"
            f"**Patrol Area:** {data.get('patrol_area', 'Unknown')}\n"
            f"**Reported On:** {time_display}\n\n"
            "**Suspicious Activity Observed:**\n"
            f"> {data.get('suspicious_activity', 'N/A')}\n\n"
            "**Suspect Description:**\n"
            f"- Gender: {data.get('suspect_gender', 'N/A')}\n"
            f"- Clothing: {data.get('clothing', 'N/A')}\n"
            f"- Affiliation: **{data.get('gang_affiliation', 'None identified')}**\n"
            f"- Vehicle: {final_vehicle}\n"
            f"- Direction of Travel: {data.get('direction_of_travel', 'N/A')}\n\n"
            "**Actions Taken:**\n"
            f"> {data.get('actions_taken', 'N/A')}\n\n"
            "**Signed,**\n"
            f"{message.author.mention} - <:LAPD_Metropolitan:1495867271501975552> | {rank}\n\n"
            "**━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━**"
        )

        embed = discord.Embed(description=desc, color=discord.Color.dark_blue())
        embed.set_footer(
            text=f"AAR Serial: {int(time.time())} | Rapid Log Engine",
            icon_url=message.author.display_avatar.url if message.author.display_avatar else None,
        )

        # Route and Save
        fallback = await self._resolve_output_channel(None, "after_action")
        target_channel = await self._get_target_channel(message.author.id, "after_action", fallback)
        
        log_entry = {
            "officer_id": message.author.id,
            "type": "rapid_log",
            "content": desc,
            "timestamp": datetime.datetime.now()
        }
        await self.aar_logs.insert_one(log_entry)
        
        # Cross-pollinate S.I.M.O.N. Suspect Intelligence
        sus_name = data.get('suspect_name')
        if sus_name and sus_name.lower() not in ["null", "none", "unknown"]:
            await self.suspect_logs.insert_one({
                "suspect_name": sus_name.lower().strip(),
                "gang":         self._normalize_gang_shorthand(data.get('gang_affiliation')),
                "officer_id":   message.author.id,
                "crimes":       data.get('suspicious_activity', 'Observed in AAR'),
                "location_raw": data.get('patrol_area', 'Unknown'),
                "postal":       self.bot.erlc_graph.resolve_target(data.get("node_id")),
                "poi":          data.get('patrol_area'),
                "entry_type":   "aar_rapid",
                "timestamp":    datetime.datetime.now()
            })

        # Award Points
        await self._award_intel_points(message.author.id, 1, "Rapid AAR Log")

        # Auto-Forward to Major Crimes Cases
        gang_tag = self._normalize_gang_shorthand(data.get('gang_affiliation'))
        if gang_tag:
            await self._forward_aar_to_cases(gang_tag, embed)

        metro_role = discord.utils.get(message.guild.roles, name="Metro Chief Inspector")
        await target_channel.send(content=metro_role.mention if metro_role else None, embed=embed)
        
        return True

    async def _forward_aar_to_cases(self, gang_tag: str, embed: discord.Embed):
        """Finds active cases for a specific gang and forwards the report."""
        cursor = self.metro_cases.find({"gang_tag": gang_tag})
        async for case in cursor:
            thread = self.bot.get_channel(case["thread_id"])
            if thread:
                forward_embed = embed.copy()
                forward_embed.title = "📂 Gang Intelligence Forward | Major Crimes"
                forward_embed.color = discord.Color.dark_red()
                await thread.send(embed=forward_embed)

    async def load_config(self):
        doc = await self.settings.find_one({"_id": "guild_config"})
        self.config_cache = doc if doc else {}

    async def _resolve_output_channel(self, interaction: discord.Interaction, key: str):
        channels = self.config_cache.get("channels", {})
        channel_id = channels.get(key)

        if channel_id:
            channel = self.bot.get_channel(int(channel_id))
            if channel:
                return channel

        return interaction.channel

    def _check_permission(self, interaction: discord.Interaction, cmd_name: str):
        return self._check_member_permission(interaction.user, cmd_name)

    def _check_member_permission(self, member: discord.Member, cmd_name: str):
        """Reusable permission check for both interactions and messages."""
        if member.id == OWNER_UID:
            return True

        perms = self.config_cache.get("permissions", {})
        allowed_roles = perms.get(cmd_name, [])

        if not allowed_roles:
            # Default behavior for Rapid AAR: require MET role if no specific roles set in dashboard
            if cmd_name == "metro_rapid_aar":
                return any("[𝐌𝐄𝐓]" in role.name for role in member.roles)
            return True

        return any(role.id in allowed_roles for role in member.roles)

    async def _get_target_channel(self, user_id: int, link_key: str, fallback: discord.abc.Messageable):
        """Resolves a linked thread for a user from MongoDB, or returns fallback."""
        data = await self.user_links.find_one({"_id": user_id})
        if data:
            channel_id = data.get(f"{link_key}_thread")
            if channel_id:
                target = self.bot.get_channel(channel_id)
                if target:
                    return target
        return fallback

    def _get_user_rank(self, member: discord.Member) -> str:
        """Helper to find the highest Metro role for signature purposes."""
        if not hasattr(member, 'roles'):
            return "Metro Operative"
        # Roles are already sorted by position; reversed() is O(N) without the O(N log N) sort cost
        for role in reversed(member.roles):
            if "[𝐌𝐄𝐓]" in role.name:
                return role.name
        return "Metro Operative"

    def _is_high_command(self, member: discord.Member) -> bool:
        """Checks if member is CI, DCI, DCO, or CO."""
        if member.id == OWNER_UID: return True
        if "[𝐌𝐄𝐓] Chief Inspector" in [r.name for r in member.roles]:
            return True
        if "[𝐌𝐄𝐓] Detective Chief Inspector" in [r.name for r in member.roles]:
            return True
        return any(any(rank in role.name for rank in self.HIGH_COMMAND_RANKS) for role in member.roles)

    def _is_senior_high_command(self, member: discord.Member) -> bool:
        """Checks if member is DCO or CO."""
        if "[𝐌𝐄𝐓] Deputy Commanding Officer" in [r.name for r in member.roles]:
            return True
        if member.id == OWNER_UID: return True
        return any(any(rank in role.name for rank in self.SENIOR_HIGH_COMMAND_RANKS) for role in member.roles)

    async def _award_intel_points(self, user_id: int, points: int, reason: str):
        """Increments an officer's Weekly Score and Career Tokens."""
        now = datetime.datetime.now(datetime.timezone.utc)
        multiplier = 1.0

        # Check for active 24hr multiplier
        stats = await self.officer_stats.find_one({"_id": user_id})
        if stats and "multiplier_expiry" in stats:
            expiry = stats["multiplier_expiry"]
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=datetime.timezone.utc)
            
            if now < expiry:
                multiplier = 1.5
                reason = f"{reason} [1.5x Multiplier]"

        weekly_gain = int(points * multiplier)
        # Career tokens are earned at a controlled rate (50% of base points, min 1)
        token_gain = max(1, int((points * 0.5) * multiplier)) if points > 0 else 0

        await self.officer_stats.update_one(
            {"_id": user_id},
            {
                "$inc": {
                    "intel_points": token_gain,  # Career Tokens (Permanent)
                    "weekly_points": weekly_gain # Weekly Score (Resets)
                },
                "$push": {"history": {
                    "reason": reason, 
                    "weekly_gain": weekly_gain, 
                    "token_gain": token_gain, 
                    "timestamp": now
                }}
            },
            upsert=True
        )
    TRAINEE_ROLE_NAME = "[𝐌𝐄𝐓] Awaiting Training Ping"

    def _count_trainees(self) -> int:
        """Count guild members currently holding the trainee role."""
        for guild in self.bot.guilds:
            role = discord.utils.get(guild.roles, name=self.TRAINEE_ROLE_NAME)
            if role:
                return len(role.members)
        return 0

    def _build_tracker_components(self, trainee_count: int) -> list:
        """Build the Components V2 payload for the training tracker."""
        import datetime as _dt

        # Threshold check for the footer note
        threshold_note = (
            "> ⚠️  **Exception active** — fewer than 4 trainees are available. "
            "Training sessions are **not required** this week."
            if trainee_count < 4
            else
            "> ✅  4 or more trainees are available — all training personnel "
            "are **required** to host at least **1 session this week**."
        )

        last_refresh = _dt.datetime.now(_dt.timezone.utc).strftime("%d %b %Y • %H:%M UTC")

        return [
            {
                "type": 17,                     # Container
                "accent_color": 0x1a1aff,       # Metro blue
                "components": [

                    # Banner image
                    {
                        "type": 12,             # Media Gallery
                        "items": [{"media": {"url": "attachment://met-training.png"}}]
                    },

                    {"type": 14, "divider": True, "spacing": 1},

                    # Title
                    {
                        "type": 10,
                        "content": "## 📋  Training Personnel — Weekly Requirements"
                    },

                    {"type": 14, "divider": False, "spacing": 1},

                    # Trainee counter
                    {
                        "type": 10,
                        "content": (
                            f"### 👥  Available Trainees\n"
                            f"**`{trainee_count}`** member{'s' if trainee_count != 1 else ''} "
                            f"currently hold the **{self.TRAINEE_ROLE_NAME}** role.\n\n"
                            f"{threshold_note}\n\n"
                            "All training personnel are **required to host a minimum of "
                            "1 training session per week**, unless fewer than 4 trainees "
                            "are available."
                        )
                    },

                    {"type": 14, "divider": True, "spacing": 1},

                    # Footer row: last refresh + docs button
                    {
                        "type": 10,
                        "content": f"-# 🔄  Last refreshed: {last_refresh}"
                    },
                    {
                        "type": 1,
                        "components": [
                            {
                                "type": 2,
                                "style": 5,
                                "label": "📄  View Training Guidelines",
                                "url": (
                                    "https://docs.google.com/document/d/"
                                    "1nSrMkEKAcb4Yt-VPxInViR4ajw_8rMeEvHlCs84e_8s"
                                    "/edit?tab=t.jwm7dfvvi2e4"
                                )
                            }
                        ]
                    }

                ]
            }
        ]

    async def _post_training_tracker(self, channel_id: int):
        """
        Delete any previously stored tracker message, then post a fresh one.
        Stores the new message ID in MongoDB so the next refresh can delete it.
        Uses raw HTTP because discord.py has no native Components V2 support.
        """
        bot_token = self.bot.http.token

        headers = {
            "Authorization": f"Bot {bot_token}",
            "Content-Type": "application/json",   # overridden for multipart below
        }

        # ── 1. Delete old message if we have one ──────────────────────
        doc = await self.settings.find_one({"_id": "guild_config"})
        old_msg_id = doc.get("training_tracker_message_id") if doc else None

        if old_msg_id:
            delete_url = (
                f"https://discord.com/api/v10/channels/{channel_id}"
                f"/messages/{old_msg_id}"
            )
            async with aiohttp.ClientSession() as session:
                async with session.delete(delete_url, headers={"Authorization": f"Bot {bot_token}"}) as resp:
                    if resp.status not in (200, 204, 404):
                        print(f"[tracker] Could not delete old message: {resp.status}")

        # ── 2. Build payload ──────────────────────────────────────────
        trainee_count = self._count_trainees()
        components = self._build_tracker_components(trainee_count)

        payload = {
            "flags": 32768,         # IS_COMPONENTS_V2
            "components": components,
        }

        post_url = f"https://discord.com/api/v10/channels/{channel_id}/messages"

        # ── 3. Try to attach image, fall back to text-only if missing ─
        try:
            with open("met-training.png", "rb") as f:
                image_bytes = f.read()

            async with aiohttp.ClientSession() as session:
                form = aiohttp.FormData()
                form.add_field(
                    "payload_json",
                    json.dumps(payload),
                    content_type="application/json",
                )
                form.add_field(
                    "files[0]",
                    image_bytes,
                    filename="met-training.png",
                    content_type="image/png",
                )
                async with session.post(
                    post_url,
                    data=form,
                    headers={"Authorization": f"Bot {bot_token}"},
                ) as resp:
                    if resp.status in (200, 201):
                        data = await resp.json()
                        new_msg_id = data["id"]
                    else:
                        print(f"[tracker] POST failed: {resp.status} – {await resp.text()}")
                        return

        except FileNotFoundError:
            # No banner image on disk — strip the Media Gallery component and send text-only
            text_only_components = [
                {
                    **payload["components"][0],
                    "components": [
                        c for c in payload["components"][0]["components"]
                        if c.get("type") != 12
                    ],
                }
            ]
            text_only_payload = {**payload, "components": text_only_components}

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    post_url,
                    json=text_only_payload,
                    headers={
                        "Authorization": f"Bot {bot_token}",
                        "Content-Type": "application/json",
                    },
                ) as resp:
                    if resp.status in (200, 201):
                        data = await resp.json()
                        new_msg_id = data["id"]
                    else:
                        print(f"[tracker] fallback POST failed: {resp.status}")
                        return

        # ── 4. Persist new message ID ─────────────────────────────────
        await self.settings.update_one(
            {"_id": "guild_config"},
            {"$set": {"training_tracker_message_id": new_msg_id}},
            upsert=True,
        )
        await self.load_config()
        print(f"[tracker] Posted training tracker (msg {new_msg_id}, {trainee_count} trainees)")

    async def _training_tracker_loop(self):
        """Background task: refresh the training tracker every 24 hours."""
        await self.bot.wait_until_ready()

        while not self.bot.is_closed():
            try:
                channels = self.config_cache.get("channels", {})
                channel_id = channels.get("training_tracker")

                if channel_id:
                    await self._post_training_tracker(int(channel_id))
                else:
                    print("[tracker] No training_tracker channel configured — skipping refresh.")

            except asyncio.CancelledError:
                return
            except Exception as exc:
                print(f"[tracker] Refresh error: {exc}")

            # Wait 24 hours before the next refresh
            await asyncio.sleep(86400)
    # ------------------------------------------------------------------ #
    # /metro_dashboard                                                   #
    # ------------------------------------------------------------------ #

    @app_commands.command(name="metro_dashboard", description="Administrative control panel for Metropolitan Unit systems.")
    @app_commands.check(lambda i: i.user.id == OWNER_UID)
    async def metro_dashboard(self, interaction: discord.Interaction):
        """Main entry point for owner-only configuration."""
        await self.load_config()
        await interaction.response.send_message(embed=_dashboard_embed(self), view=DashboardView(self), ephemeral=True)

    # ------------------------------------------------------------------ #
    # /metro_link                                                        #
    # ------------------------------------------------------------------ #

    @app_commands.command(
        name="metro_link",
        description="Manage your profile's reporting thread links (Link/Unlink).",
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Link", value="link"),
            app_commands.Choice(name="Unlink", value="unlink"),
        ],
        link_type=[
            app_commands.Choice(name="After Action Report Thread", value="after_action"),
            app_commands.Choice(name="K9 Deployment Thread",     value="k9"),
        ]
    )
    async def metro_link(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        link_type: app_commands.Choice[str],
        thread: discord.Thread,
    ):
        """Maps a user to a specific thread ID in MongoDB or removes it."""
        if action.value == "link":
            if not thread:
                await interaction.response.send_message("❌ You must provide a thread to link.", ephemeral=True)
                return
            
            try:
                await self.user_links.update_one(
                    {"_id": interaction.user.id},
                    {"$set": {f"{link_type.value}_thread": thread.id}},
                    upsert=True
                )
                
                embed = discord.Embed(
                    description=(
                        "## <:LAPD_Metropolitan:1495867271501975552> | Thread Linked\n"
                        "**━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━**\n"
                        f"Your **{link_type.name}** is now linked to {thread.mention}.\n"
                        f"Future reports of this type will be automatically routed there."
                    ),
                    color=discord.Color.green(),
                    timestamp=datetime.datetime.now(datetime.timezone.utc)
                )
                
                await thread.send(embed=embed)
                await interaction.response.send_message(f"✅ Successfully linked your **{link_type.name}** to {thread.mention}.", ephemeral=True)
            except Exception as e:
                if not interaction.response.is_done():
                    await interaction.response.send_message(f"❌ Database Error: Failed to link thread. ({e})", ephemeral=True)
                else:
                    await interaction.followup.send(f"❌ Database Error: Failed to link thread. ({e})", ephemeral=True)
        else:
            try:
                await self.user_links.update_one(
                    {"_id": interaction.user.id},
                    {"$unset": {f"{link_type.value}_thread": ""}}
                )
                
                embed = discord.Embed(
                    description=(
                        "## <:LAPD_Metropolitan:1495867271501975552> | Thread Unlinked\n"
                        "**━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━**\n"
                        f"Your **{link_type.name}** link has been removed from your profile.\n"
                        "Reports will now be routed to default Unit channels."
                    ),
                    color=discord.Color.red()
                )
                await interaction.response.send_message(embed=embed)
            except Exception as e:
                await interaction.response.send_message(
                    f"❌ Database Error: Failed to unlink thread. ({e})", ephemeral=True
                )

    # ------------------------------------------------------------------ #
    # /metro_log_training                                                  #
    # ------------------------------------------------------------------ #

    @app_commands.command(
        name="metro_log_training",
        description="Log results for a Metropolitan Unit training session.",
    )
    @app_commands.choices(training_format=[
        app_commands.Choice(name="Entry Training", value="entry"),
        app_commands.Choice(name="K9 Training", value="k9"),
    ])
    async def metro_log_training(
        self,
        interaction: discord.Interaction,
        trainee: discord.Member,
        outcome: str,
        notes: str,
        training_format: app_commands.Choice[str],
        co_host: discord.Member = None,
    ):
        selected_format = training_format.value if training_format else "entry"
        modal_cls = K9TrainingModal if selected_format == "k9" else MetroTrainingModal
        await interaction.response.send_modal(
            modal_cls(
                host=interaction.user,
                co_host=co_host,
                trainee=trainee,
                outcome=outcome,
                notes=notes,
            )
        )

    # ------------------------------------------------------------------ #
    # /metro_promote                                                       #
    # ------------------------------------------------------------------ #

    @app_commands.command(
        name="metro_promote",
        description="Issue a promotion to an officer.",
    )
    async def metro_promote(
        self,
        interaction: discord.Interaction,
        officer: discord.Member,
        previous_rank: str,
        new_rank: str,
        notes: str,
        signed: str,
    ):
        await interaction.response.defer(ephemeral=True)

        if not self._check_permission(interaction, "metro_promote"):
            await interaction.followup.send("❌ You do not have the required Metropolitan roles to issue promotions.", ephemeral=True)
            return

        desc = (
            "## <:LAPD_Metropolitan:1495867271501975552> | Metropolitan Promotion\n"
            f"{DASHBOARD_DIVIDER}\n\n"
            f"**Metro Operative:** {officer.mention}\n\n"
            f"**Old Rank:** {previous_rank}\n\n"
            f"**New Rank:** {new_rank}\n\n"
            f"**Notes:** {notes}\n\n"
            f"**Signed:** {signed}\n\n"
            f"{DASHBOARD_DIVIDER}\n"
        )

        embed = discord.Embed(description=desc, color=discord.Color.blue())
        embed.set_thumbnail(url="https://i.imgur.com/qdvbBqe.png")
        file = discord.File(BASE_DIR / "promotion.png", filename="promotion.png")
        embed.set_image(url="attachment://promotion.png")
        embed.set_footer(
            text=f"Issued by {interaction.user.display_name}",
            icon_url=(
                interaction.user.display_avatar.url
                if interaction.user.display_avatar
                else None
            ),
        )

        channel = await self._resolve_output_channel(interaction, "metro_promote")
        await channel.send(content=officer.mention, embed=embed, file=file)
        await interaction.followup.send(
            "✅ Promotion successfully logged!", ephemeral=True
        )

    # ------------------------------------------------------------------ #
    # /metro_announcement                                                  #
    # ------------------------------------------------------------------ #

    @app_commands.command(
        name="metro_announcement",
        description="Send a Metropolitan Unit announcement.",
    )
    @app_commands.describe(ping_type="Choose whether to ping the Unit or not")
    @app_commands.choices(
        ping_type=[
            app_commands.Choice(name="Ping Announcement",     value="ping"),
            app_commands.Choice(name="Non-Ping Announcement", value="no_ping"),
        ]
    )
    async def metro_announcement(
        self,
        interaction: discord.Interaction,
        ping_type: app_commands.Choice[str],
    ):
        metro_role = discord.utils.get(
            interaction.guild.roles, name="[𝐋𝐀𝐏𝐃] Metropolitan Unit"
        )
        await interaction.response.send_modal(
            MetroAnnouncementModal(
                ping_role=ping_type.value == "ping",
                role=metro_role,
            )
        )

    # ------------------------------------------------------------------ #
    # /metro_infract                                                       #
    # ------------------------------------------------------------------ #

    @app_commands.command(
        name="metro_infract",
        description="Issue an infraction to an officer.",
    )
    async def metro_infract(
        self,
        interaction: discord.Interaction,
        officer: discord.Member,
        punishment: str,
        reason: str,
        appealable: str,
        signed: str,
    ):
        await interaction.response.defer(ephemeral=True)

        if not self._check_permission(interaction, "metro_infract"):
            await interaction.followup.send("❌ You do not have permission to issue infractions.", ephemeral=True)
            return

        desc = (
            "## <:LAPD_Metropolitan:1495867271501975552> | Metropolitan Infraction\n"
            f"{DASHBOARD_DIVIDER}\n\n"
            f"**Officer:** {officer.mention}\n\n"
            f"**Punishment:** {punishment}\n\n"
            f"**Reason:** {reason}\n\n"
            f"**Appealable:** {appealable}\n\n"
            f"**Signed:** {signed}\n\n"
            f"{DASHBOARD_DIVIDER}\n"
        )

        embed = discord.Embed(description=desc, color=discord.Color.red())
        embed.set_thumbnail(url="https://i.imgur.com/qdvbBqe.png")
        file = discord.File(BASE_DIR / "infraction.png", filename="infraction.png")
        embed.set_image(url="attachment://infraction.png")
        embed.set_footer(
            text=f"Issued by {interaction.user.display_name}",
            icon_url=(
                interaction.user.display_avatar.url
                if interaction.user.display_avatar
                else None
            ),
        )

        channel = await self._resolve_output_channel(interaction, "metro_infract")
        await channel.send(content=officer.mention, embed=embed, file=file)
        await interaction.followup.send(
            "✅ Infraction has been posted successfully.", ephemeral=True
        )

    # ------------------------------------------------------------------ #
    # /metro_mass_shift                                                    #
    # ------------------------------------------------------------------ #

    @app_commands.command(
        name="metro_mass_shift",
        description="Announce a Metropolitan Unit mass shift.",
    )
    async def metro_mass_shift(
        self,
        interaction: discord.Interaction,
        co_host: discord.Member = None,
        notes: str = None,
    ):
        await interaction.response.defer(ephemeral=True)

        metro_role = discord.utils.get(
            interaction.guild.roles, name="[𝐋𝐀𝐏𝐃] Metropolitan Unit"
        )
        if not metro_role:
            await interaction.followup.send(
                "❌ Metropolitan Unit role not found.", ephemeral=True
            )
            return

        host = interaction.user
        desc = (
            "## <:LAPD_Metropolitan:1495867271501975552> ︱ Metropolitan Mass Shift\n"
            f"{DASHBOARD_DIVIDER}\n"
            "Metropolitan Operatives are needed in-game. Join up, gear-up, and set up for a fun shift!\n\n"
            f"**Hosted By:** {host.mention}\n\n"
            f"**Co-Host:** {co_host.mention if co_host else 'None'}\n"
            f"**Notes:** {notes}\n"
            f"{DASHBOARD_DIVIDER}\n"
            "**Reactions:**\n✅ = Coming\n❔ = Maybe\n❌ = Unable\n"
        )

        embed = discord.Embed(description=desc, color=discord.Color.blue())
        embed.set_thumbnail(url="https://i.imgur.com/qdvbBqe.png")
        embed.set_footer(
            text=f"Issued by {host.display_name}",
            icon_url=host.display_avatar.url if host.display_avatar else None,
        )

        await interaction.followup.send(
            "✅ Mass Shift Issued", ephemeral=True
        )
        channel = await self._resolve_output_channel(interaction, "metro_mass_shift")
        msg = await channel.send(content=metro_role.mention, embed=embed)

        try:
            for emoji in ("✅", "❔", "❌"):
                await msg.add_reaction(emoji)
        except Exception as e:
            print(f"[MASS SHIFT REACTION ERROR] {e}")

    # ------------------------------------------------------------------ #
    # /host_metro_training                                                 #
    # ------------------------------------------------------------------ #

    @app_commands.command(
        name="host_metro_training",
        description="Host a Metropolitan Unit training session.",
    )
    async def host_metro_training(
        self,
        interaction: discord.Interaction,
        co_host: discord.Member = None,
        start_time: str = "TBD",
    ):
        await interaction.response.defer(ephemeral=True)

        ping_role = discord.utils.get(
            interaction.guild.roles, name="[𝐌𝐄𝐓] Awaiting Training Ping"
        )
        if not ping_role:
            await interaction.followup.send(
                "❌ Metropolitan Unit role not found.", ephemeral=True
            )
            return

        host = interaction.user
        desc = (
            "## <:LAPD_Metropolitan:1495867271501975552> | Metropolitan Entry Training\n"
            f"{DASHBOARD_DIVIDER}\n\n"
            f"**Host:** {host.mention}\n\n"
            f"**Co-Host:** {co_host.mention if co_host else 'N/A'}\n\n"
            f"**Starting Time:** {start_time}\n\n"
            "**Weaponry Trainings** are hands-on trainings in which you, the trainee, "
            "undergo several scenarios designed to evaluate your performance and future "
            "in the Metropolitan Unit.\nIf you are a trainer, contact the host to "
            "join as a co-host!\n"
            "This training consists of:\n"
            "• Active Shooter Exercise\n"
            "• Undercover (UC) Exercise\n"
            "• Protection Detail Exercise\n\n"
            f"{DASHBOARD_DIVIDER}\n"
        )

        embed = discord.Embed(description=desc, color=discord.Color.blue())
        embed.set_thumbnail(url="https://i.imgur.com/qdvbBqe.png")
        file = discord.File(BASE_DIR / "training-sesh.png", filename="training-sesh.png")
        embed.set_image(url="attachment://training-sesh.png")
        embed.set_footer(
            text=f"Announced by {host.display_name}",
            icon_url=host.display_avatar.url if host.display_avatar else None,
        )

        await interaction.followup.send(
            "✅ Host training issued.", ephemeral=True
        )
        channel = await self._resolve_output_channel(interaction, "host_metro_training")
        msg = await channel.send(content=ping_role.mention, embed=embed, file=file)

        try:
            for emoji in ("✅", "❔", "❌"):
                await msg.add_reaction(emoji)
        except Exception as e:
            print(f"[TRAINING REACTION ERROR] {e}")

    # ------------------------------------------------------------------ #
    # /metro_openings                                                      #
    # ------------------------------------------------------------------ #

    @app_commands.command(
        name="metro_openings",
        description="Display current roster and openings for Metro Unit ranks.",
    )
    async def metro_openings(self, interaction: discord.Interaction):
        await interaction.response.defer()

        guild = interaction.guild
        if not guild:
            await interaction.followup.send("This command must be run in a guild.")
            return

        if guild.member_count != len(guild.members):
            await guild.chunk()

        rank_groups = [
            ("     [MET] Directorate     ", [
                ("[𝐌𝐄𝐓] Commanding Officer",1),
                ("[𝐌𝐄𝐓] Deputy Commanding Officer",4),
            ]),
            (" [MET] Command Inspector General ", [
                ("[𝐌𝐄𝐓] Detective Chief Inspector",4),
                ("[𝐌𝐄𝐓] Chief Inspector",4),
            ]),
            ("[MET] General Supervisory Staff", [
                ("[𝐌𝐄𝐓] Supervisory Sergeant", 5),
            ]),
            ("  [MCS] Major Crimes Detectives  ", [
                ("[𝐌𝐄𝐓] Senior Detective", 7),
                ("[𝐌𝐄𝐓] Junior Detective", 20),
            ]),
            ("   [MET] B/C Platoon Operatives  ", [
                ("[𝐌𝐄𝐓] Senior Officer", 20),
                ("[𝐌𝐄𝐓] Junior Officer", 20),
            ]),
            ("[MET] Probationary Rank Openings", [
                ("[𝐌𝐄𝐓] Probationary Officer", 20),
            ]),
        ]

        seal          = "<:LAPD_Metropolitan:1495867271501975552>"
        divider       = f"<:line:1500739607568842865>" * 22
        embed_color   = discord.Color.from_rgb(5, 164, 232)
        embed_list    = []

        # title embed
        embed_list.append(
            discord.Embed(
                description=f"# {seal} **Metropolitan Unit Openings**\n{divider}\n\n",
                color=embed_color,
            )
        )

        for group_name, ranks in rank_groups:
            desc_parts = [f"## {seal} **{group_name}** {seal}\n"]

            for rank_name, quota in ranks:
                role    = discord.utils.get(guild.roles, name=rank_name)
                members = role.members if role else []
                count   = len(members)

                desc_parts.append(f"{divider}\n**{rank_name}**\n{divider}\n")
                if not members:
                    desc_parts.append("• No officers currently hold this rank.\n")
                else:
                    desc_parts.append("\n".join(f"• {m.mention}" for m in members) + "\n")

                desc_parts.append(
                    f"→ **Closed Spots:** {count}/{quota}\n"
                    f"→ **Open Spots:** {max(0, quota - count)}/{quota}\n\n"
                )

            desc_parts.append(divider)
            embed_list.append(
                discord.Embed(description="".join(desc_parts), color=embed_color)
            )
        # already deferred earlier → DO NOT defer again

        channel = await self._resolve_output_channel(interaction, "metro_openings")

        for embed in embed_list:
            await channel.send(embed=embed)
            await asyncio.sleep(0.5)

        await interaction.followup.send(
            "✅ Openings have been updated successfully.",
            ephemeral=True
        )

    # ------------------------------------------------------------------ #
    # /metro_start_live                                                    #
    # ------------------------------------------------------------------ #

    @app_commands.command(
        name="metro_start_live",
        description="Initialize a live Metropolitan operation with element assignments and map briefing.",
    )
    @app_commands.describe(
        postal="The postal or POI where the operation is focused.",
        operatives="Mention all operatives in the op (e.g. @User1 @User2).",
        start_time="Optional: Set a future time for the operation (e.g. 8:00 PM EST). Defaults to Immediate.",
        target_gang="The criminal faction targeted by this operation.",
        warrant_id="Optional: The warrant ID associated with this operation (e.g. LAPD4512)."
    )
    @app_commands.choices(target_gang=[
        app_commands.Choice(name="None / Unaffiliated", value="None"),
        app_commands.Choice(name="77th Saints Gang", value="77th"),
        app_commands.Choice(name="West Coast Cartel", value="WCC"),
        app_commands.Choice(name="Noche Silente Hermanos", value="NSH"),
    ])
    async def metro_start_live(
        self, interaction: discord.Interaction, postal: str, operatives: str, start_time: str = "Immediate", target_gang: str = "None", warrant_id: str = None):
        import re

        user_ids = list(set(re.findall(r'\d{17,19}', operatives)))
        members  = [m for uid in user_ids if (m := interaction.guild.get_member(int(uid)))]

        if not members:
            return await interaction.response.send_message(
                "❌ No valid operatives found. Please mention them or provide their IDs.",
                ephemeral=True,
            )

        resolved = self.bot.erlc_graph.resolve_target(postal)
        if not resolved:
            return await interaction.response.send_message(
                f"❌ Postal `{postal}` could not be resolved in the map database.",
                ephemeral=True,
            )

        embed = _embed_setup(interaction.user, postal, {}, members, start_time, target_gang, warrant_id)
        await interaction.response.send_message(
            embed=embed,
            view=LiveOpAssignmentView(self, interaction.user, postal, members, start_time, target_gang, warrant_id),
            ephemeral=True,
        )
    # ==========================================
    # TRAINING INFO COMMAND (Components V2)
    # ==========================================
    @app_commands.command(name="metro_training_info", description="Display training requirements for personnel.")
    async def metro_training_info(self, interaction: discord.Interaction):
        await interaction.response.defer()
 
        components = [
            {
                "type": 17,                     # Container
                "accent_color": 0x1a1aff,       # Metro blue stripe
                "components": [
 
                    # ── Banner image ──
                    {
                        "type": 12,             # Media Gallery
                        "items": [
                            {
                                "media": {
                                    "url": "attachment://met-training.png"
                                }
                            }
                        ]
                    },
 
                    # ── Separator ──
                    {
                        "type": 14,             # Separator
                        "divider": True,
                        "spacing": 1
                    },
 
                    # ── Title ──
                    {
                        "type": 10,             # Text Display
                        "content": "## 📋  Training Personnel — Requirements"
                    },
 
                    # ── Spacer ──
                    {
                        "type": 14,
                        "divider": False,
                        "spacing": 1
                    },
 
                    # ── Body copy ──
                    {
                        "type": 10,
                        "content": (
                            "All training personnel are **required to host a minimum of "
                            "1 training session per week**.\n\n"
                            "> ⚠️  **Exception:** If there are **fewer than 4 available trainees**, "
                            "you are **not** required to host that week. Document the shortfall "
                            "in the training log so command is aware."
                        )
                    },
 
                    # ── Separator before button ──
                    {
                        "type": 14,
                        "divider": True,
                        "spacing": 1
                    },
 
                    # ── Google Docs link button ──
                    {
                        "type": 1,              # Action Row
                        "components": [
                            {
                                "type": 2,      # Button
                                "style": 5,     # LINK
                                "label": "📄  View Training Guidelines",
                                "url": "https://docs.google.com/document/d/1nSrMkEKAcb4Yt-VPxInViR4ajw_8rMeEvHlCs84e_8s/edit?tab=t.jwm7dfvvi2e4"
                            }
                        ]
                    }
 
                ]
            }
        ]
 
        # ── discord.py has no native Components V2 support, so we hit the
        #    REST API directly via the followup webhook URL. ──────────────
        webhook_url = (
            f"https://discord.com/api/v10/webhooks/"
            f"{interaction.application_id}/{interaction.token}"
        )
 
        payload = {
            "flags": 32768,             # IS_COMPONENTS_V2  (1 << 15)
            "components": components
        }
 
        try:
            with open("met-training.png", "rb") as f:
                image_bytes = f.read()
 
            async with aiohttp.ClientSession() as session:
                form = aiohttp.FormData()
                form.add_field(
                    "payload_json",
                    json.dumps(payload),
                    content_type="application/json"
                )
                form.add_field(
                    "files[0]",
                    image_bytes,
                    filename="met-training.png",
                    content_type="image/png"
                )
 
                async with session.post(webhook_url, data=form) as resp:
                    if resp.status not in (200, 204):
                        body = await resp.text()
                        print(f"[training_info] Discord API error {resp.status}: {body}")
                        await interaction.followup.send(
                            "❌ Failed to send training info. Check bot logs.",
                            ephemeral=True
                        )
 
        except FileNotFoundError:
            # Image missing — send without it
            payload_no_image = {
                "flags": 32768,
                "components": [
                    {
                        "type": 17,
                        "accent_color": 0x1a1aff,
                        "components": [
                            comp for comp in components[0]["components"]
                            if comp.get("type") != 12   # strip Media Gallery
                        ]
                    }
                ]
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    webhook_url,
                    json=payload_no_image
                ) as resp:
                    if resp.status not in (200, 204):
                        print(f"[training_info] fallback error {resp.status}: {await resp.text()}")
 
        except Exception as e:
            print(f"[training_info] Unexpected error: {e}")
            await interaction.followup.send(
                "❌ Unexpected error sending training info.",
                ephemeral=True
            )
    # ------------------------------------------------------------------ #
    # /metro_after_action                                                 #
    # ------------------------------------------------------------------ #

    @app_commands.command(
        name="metro_after_action",
        description="Create a Metropolitan After Action Report.",
    )
    @app_commands.describe(
        officers="Name(s) or mention(s) of involved officers",
        vehicle="Select suspect vehicle (searchable by brand/model/real car)"
    )
    @app_commands.choices(gang=[
        app_commands.Choice(name="None / Unaffiliated", value="none"),
        app_commands.Choice(name="77th Saints Gang (77th)", value="77th"),
        app_commands.Choice(name="West Coast Cartel (WCC)", value="WCC"),
        app_commands.Choice(name="Noche Silente Hermanos (NSH)", value="NSH"),
    ])
    @app_commands.choices(report_type=[
        app_commands.Choice(name="Standard Patrol / Field Report", value="standard"),
        app_commands.Choice(name="Raid / Stakeout / Special Ops", value="special")
    ])
    async def metro_after_action(
        self,
        interaction: discord.Interaction,
        officers: str,
        patrol_area: str,
        suspect_name: str,
        time_observed: str,
        suspicious_activity: str,
        actions_taken: str,
        vehicle: str,
        gang: app_commands.Choice[str],
        report_type: app_commands.Choice[str],
        additional_notes: str = None,
        suspect_gender: str = None,
        clothing: str = None,
        direction_of_travel: str = None,
    ):
        unix_time = None
        try:
            now = datetime.datetime.now()
            parsed = datetime.datetime.strptime(time_observed.strip(), "%H:%M")
            combined = now.replace(hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0)
            unix_time = int(combined.timestamp())
        except Exception:
            unix_time = None

        await interaction.response.defer(ephemeral=True)

        suspect_gender = suspect_gender if suspect_gender else "N/A"
        clothing = clothing if clothing else "N/A"
        vehicle = vehicle if vehicle in VEHICLE_LOOKUP else "Unknown Vehicle"
        direction_of_travel = direction_of_travel if direction_of_travel else "N/A"

        time_display = f"<t:{unix_time}:F>" if unix_time else "N/A"
        rank = self._get_user_rank(interaction.user)

        # Resolve location for S.I.M.O.N. analytics
        simon_cog = self.bot.get_cog("Simon")
        extracted_node = None
        if simon_cog:
            prompt = f"""
            Map this patrol area to a valid Node ID.
            Input: {patrol_area}
            Nodes: {simon_cog._nodes_prompt_cache}
            Return ONLY JSON: {{"node_id": "N-XXX", "poi": "name"}}
            """
            from llm import call_llm
            loc_res = await call_llm(prompt)
            if loc_res:
                extracted_node = self.bot.erlc_graph.resolve_target(loc_res.get("node_id"))

        # Auto-pollinate Suspect Logs for watchlist and profile updates
        if suspect_name and suspect_name.lower() not in ["none", "n/a", "unknown"]:
            await self.suspect_logs.insert_one({
                "suspect_name": suspect_name.lower().strip(),
                "gang":         gang.value if gang.value != "none" else None,
                "officer_id":   interaction.user.id,
                "crimes":       suspicious_activity,
                "location_raw": patrol_area,
                "postal":       extracted_node,
                "entry_type":   "aar_formal",
                "timestamp":    datetime.datetime.now(datetime.timezone.utc)
            })

        metro_role = discord.utils.get(interaction.guild.roles, name="Metro Chief Inspector")

        desc = (
            "## <:LAPD_Metropolitan:1495867271501975552> | After Action Report\n"
            f"{DASHBOARD_DIVIDER}\n\n"
            f"**Officer(s):** {officers}\n"
            f"**Patrol Area:** {patrol_area}\n"
            f"**Reported At:** {time_display}\n\n"
            "**Suspicious Activity Observed:**\n"
            f"> {suspicious_activity}\n\n"
            "**Suspect Description:**\n"
            f"- Gender: {suspect_gender}\n"
            f"- Clothing: {clothing}\n"
            f"- Affiliation: **{gang.name}**\n"
            f"- Vehicle: {vehicle}\n"
            f"- Direction of Travel: {direction_of_travel}\n\n"
            "**Actions Taken:**\n"
            f"> {actions_taken}\n\n"
            "**Additional Notes:**\n"
            f"> {additional_notes}\n\n"
            "**Signed,**\n"
            f"{interaction.user.mention} - <:LAPD_Metropolitan:1495867271501975552> | {rank}\n\n"
            f"{DASHBOARD_DIVIDER}"
        )

        embed = discord.Embed(description=desc, color=discord.Color.dark_blue())
        embed.set_footer(
            text=f"AAR Serial: {int(time.time())} | Issued by {interaction.user.display_name}",
            icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None,
        )

        # Route to linked thread or default patrol files channel
        fallback = await self._resolve_output_channel(interaction, "after_action")
        target_channel = await self._get_target_channel(interaction.user.id, "after_action", fallback)
        
        # Save to DB and Award Points
        log_entry = {
            "officer_id": interaction.user.id,
            "type": report_type.value,
            "content": desc,
            "timestamp": datetime.datetime.now()
        }
        await self.aar_logs.insert_one(log_entry)
        
        points = 2 if report_type.value == "special" else 1
        await self._award_intel_points(interaction.user.id, points, f"AAR: {report_type.name}")

        # Auto-Forward to Major Crimes Cases
        if gang.value != "none":
            await self._forward_aar_to_cases(gang.value, embed)

        await target_channel.send(
            content=metro_role.mention if metro_role else None,
            embed=embed,
        )

        await interaction.followup.send(
            f"✅ After Action Report posted. (+{points} Intel Points)", ephemeral=True
        )

    @metro_after_action.autocomplete("vehicle")
    async def vehicle_autocomplete(self, interaction: discord.Interaction, current: str):
        current = current.lower()
        return [
            app_commands.Choice(name=v["label"][:100], value=v["label"])
            for v in VEHICLE_SEARCH_LIST
            if current in v["search"]
        ][:25]

    # ------------------------------------------------------------------ #
    # /request_metro                                                       #
    # ------------------------------------------------------------------ #

    @app_commands.command(
        name="request_metro",
        description="Request Metropolitan x SWAT response for active incidents",
    )
    async def request_metro(
        self, interaction: discord.Interaction, reason: str
    ):
        await interaction.response.defer(ephemeral=True)

        if not self._check_permission(interaction, "request_metro"):
            await interaction.followup.send("❌ You do not have permission to request Metropolitan assistance.", ephemeral=True)
            return

        now        = time.time()
        guild_id   = interaction.guild.id
        cooldown   = self.bot.request_metro_cooldowns
        last_used  = cooldown.get(guild_id)

        # 12-hour server cooldown
        if last_used and (now - last_used) < 21_600:
            remaining = int(43_200 - (now - last_used))
            hours     = remaining // 3600
            minutes   = (remaining % 3600) // 60
            await interaction.followup.send(
                f"⏳ Command on cooldown for this server. "
                f"Try again in {hours}h {minutes}m.",
                ephemeral=True,
            )
            return

        cooldown[guild_id] = now

        guild      = interaction.guild
        metro_role = discord.utils.get(guild.roles, name="[𝐋𝐀𝐏𝐃] Metropolitan Unit")
        swat_role  = discord.utils.get(
            guild.roles, name="[𝐋𝐀𝐏𝐃] Special Weapons & Tactics"
        )

        if not metro_role or not swat_role:
            await interaction.followup.send(
                "❌ No valid response roles found.", ephemeral=True
            )
            return

        host = interaction.user
        desc = (
            "## 🚨 | ACTIVE REQUEST: METRO x SWAT DEPLOYMENT\n"
            "**━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━**\n\n"
            f"**Requested By:** {host.mention}\n\n"
            f"**Incident Type:** {reason}\n\n"
            "**Units Requested:**\n"
            f"- {metro_role.mention}\n"
            f"- {swat_role.mention}\n\n"
            "**━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━**"
        )

        embed = discord.Embed(description=desc, color=discord.Color.red())
        embed.set_footer(
            text=f"Dispatch issued by {host.display_name}",
            icon_url=host.display_avatar.url if host.display_avatar else None,
        )

        await interaction.followup.send(
            "✅ Request sent.", ephemeral=True
        )
        channel = await self._resolve_output_channel(interaction, "request_metro")
        await channel.send(
            content=f"{metro_role.mention} {swat_role.mention}",
            embed=embed,
        )

    # ------------------------------------------------------------------ #
    # /k9_deploy                                                           #
    # ------------------------------------------------------------------ #

    @app_commands.command(
        name="k9_deploy",
        description="Log a K9 deployment for Metropolitan Unit.",
    )
    async def k9_deploy(
        self,
        interaction: discord.Interaction,
        handler_name: str,
        k9_name: str,
        reason: str,
        result: str,
        evidence: discord.Attachment = None,
    ):
        await interaction.response.defer(ephemeral=True)

        evidence_text = evidence.url if evidence else "None"
        desc = (
            "## <:LAPD_Metropolitan:1495867271501975552> | K-Platoon Deployment Log\n"
            "**━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━**\n"
            f"> **Handler Name:** {handler_name}\n"
            f"> **K9 Name:** {k9_name}\n"
            f"> **Reason for Deployment:** {reason}\n"
            f"> **Result:** {result}\n"
            f"> **Evidence:** {evidence_text}\n"
            "**━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━**"
        )

        embed = discord.Embed(description=desc, color=discord.Color.dark_blue())
        embed.set_footer(
            text=f"Logged by {interaction.user.display_name}",
            icon_url=(
                interaction.user.display_avatar.url
                if interaction.user.display_avatar
                else None
            ),
        )

        # Route to linked thread or default K9 files channel
        fallback = await self._resolve_output_channel(interaction, "k9")
        target_channel = await self._get_target_channel(interaction.user.id, "k9", fallback)

        await target_channel.send(embed=embed)

        # Log and Award Points (Field Report base)
        await self.k9_logs.insert_one({
            "officer_id": interaction.user.id,
            "handler": handler_name,
            "k9": k9_name,
            "timestamp": datetime.datetime.now()
        })
        await self._award_intel_points(interaction.user.id, 1, "K9 Deployment")

        await interaction.followup.send("✅ K9 deployment logged.", ephemeral=True)

    # ------------------------------------------------------------------ #
    # /metro_start_case                                                  #
    # ------------------------------------------------------------------ #

    @app_commands.command(
        name="metro_start_case",
        description="Initialize a new Metropolitan Major Crimes case in the forum.",
    )
    async def metro_start_case(
        self, 
        interaction: discord.Interaction, 
        organised_crime_group_name: str, 
        forum_channel_id: str = None
    ):
        """Creates a unique case thread in a forum channel."""
        await interaction.response.defer(ephemeral=True)

        try:
            channels = self.config_cache.get("channels", {})
            forum_id = int(forum_channel_id) if forum_channel_id else channels.get("metro_cases")
            if not forum_id:
                await interaction.followup.send("❌ No Forum Channel ID provided or configured.", ephemeral=True)
                return

            channel = self.bot.get_channel(forum_id)
            if not isinstance(channel, discord.ForumChannel):
                await interaction.followup.send("❌ The provided ID does not belong to a Forum Channel.", ephemeral=True)
                return
        except (ValueError, TypeError):
            await interaction.followup.send("❌ Invalid Forum Channel ID provided.", ephemeral=True)
            return
            
        # Extract gang tag for future auto-forwarding
        gang_tag = self._normalize_gang_shorthand(organised_crime_group_name)

        case_id = random.randint(10000, 99999)
        thread_title = f"<:LAPD_Metropolitan:1495867271501975552> | Metropolitan Major Crimes Case - ID{case_id}"
        
        embed = discord.Embed(
            description=(
                f"## <:LAPD_Metropolitan:1495867271501975552> | Case Initialized\n"
                f"{DASHBOARD_DIVIDER}\n\n"
                f"Case has been made on **{organised_crime_group_name}**. You can now log evidence using `/metro_case_log` "
                "along with anyone else who produces evidence.\n\n"
                f"{DASHBOARD_DIVIDER}"
            ),
            color=discord.Color.dark_red()
        )
        embed.set_footer(text=f"Case ID: {case_id} | Assigned to {interaction.user.display_name}")

        try:
            thread_with_msg = await channel.create_thread(name=thread_title, embed=embed)
            
            # Persist mapping for routing logs via Case ID
            await self.metro_cases.insert_one({
                "case_id": case_id,
                "thread_id": thread_with_msg.thread.id,
                "gang_tag": gang_tag # Stored for auto-forwarding
            })

            await interaction.followup.send(
                f"✅ Case **ID{case_id}** created successfully in {thread_with_msg.thread.mention}.", 
                ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(f"❌ Error creating thread: {e}", ephemeral=True)

    # ------------------------------------------------------------------ #
    # /metro_case_log                                                    #
    # ------------------------------------------------------------------ #

    @app_commands.command(
        name="metro_case_log",
        description="Log evidence for an active Metropolitan case.",
    )
    async def metro_case_log(
        self,
        interaction: discord.Interaction,
        case_id: int,
        detectives: str,
        suspect_description: str = None,
        vehicles_used: str = None,
        suspicious_activities: str = None,
        criminal_activities: str = None,
        area: str = None,
        photo: discord.Attachment = None,
        notes: str = "None provided.",
    ):
        """Posts a structured evidence log for major crimes."""
        await interaction.response.defer(ephemeral=True)

        # Route to the specific case thread via Case ID lookup
        case_data = await self.metro_cases.find_one({"case_id": case_id})

        if not case_data:
            await interaction.followup.send(
                f"❌ Error: Case ID **{case_id}** not found in the database.", 
                ephemeral=True
            )
            return

        target_channel = self.bot.get_channel(case_data["thread_id"])
        if not target_channel:
            await interaction.followup.send("❌ Error: Could not resolve the Case Thread. It may have been deleted.", ephemeral=True)
            return

        rank = self._get_user_rank(interaction.user)
        
        desc = (
            "## <:LAPD_Metropolitan:1495867271501975552> | Major Crimes Evidence\n"
            f"{DASHBOARD_DIVIDER}\n\n"
            f"**Detective(s):**\n> {detectives}\n\n"
            f"**Suspect Description:**\n> {suspect_description}\n\n"
            f"**Vehicle(s) Used:**\n> {vehicles_used}\n\n"
            f"**Suspicious Activities:**\n> {suspicious_activities}\n"
            f"**Criminal Activities:**\n> {criminal_activities}\n\n"
            f"**Area:**\n> {area}\n\n"
            f"**Notes:**\n> {notes}\n\n"
            f"{DASHBOARD_DIVIDER}\n"
            f"**Signed,**\n"
            f"{interaction.user.mention} - {rank}\n"
        )

        embed = discord.Embed(description=desc, color=discord.Color.from_rgb(30, 33, 46))
        embed.set_thumbnail(url="https://i.imgur.com/qdvbBqe.png")
        
        if photo:
            embed.set_image(url=photo.url)

        embed.set_footer(
            text=f"Evidence Log Serial: {int(time.time())}",
            icon_url=interaction.user.display_avatar.url
        )

        await target_channel.send(embed=embed)

        # Log and Award Points
        await self.case_logs.insert_one({
            "officer_id": interaction.user.id,
            "case_id": case_id,
            "timestamp": datetime.datetime.now()
        })
        await self._award_intel_points(interaction.user.id, 1, f"Case Log: ID{case_id}")

        await interaction.followup.send("✅ Evidence log submitted. (+1 Intel Point)", ephemeral=True)

    # ------------------------------------------------------------------ #
    # POINT MANAGEMENT & STATS                                           #
    # ------------------------------------------------------------------ #

    @app_commands.command(name="metro_modify_points", description="Manually adjust an officer's intel points.")
    async def metro_modify_points(self, interaction: discord.Interaction, officer: discord.Member, amount: int, reason: str):
        if not self._is_high_command(interaction.user):
            await interaction.response.send_message("❌ Access Denied.", ephemeral=True)
            return

        await self._award_intel_points(officer.id, amount, f"Manual Adjustment: {reason}")
        await interaction.response.send_message(f"✅ Adjusted {officer.mention} by **{amount}** points. Reason: {reason}")

    @app_commands.command(name="metro_intel_profile", description="View an officer's detailed intelligence point history.")
    async def metro_intel_profile(self, interaction: discord.Interaction, officer: discord.Member):
        await interaction.response.defer()
        
        data = await self.officer_stats.find_one({"_id": officer.id})
        if not data or not data.get("history"):
            tokens = data.get("intel_points", 0) if data else 0
            weekly = data.get("weekly_points", 0) if data else 0
            await interaction.followup.send(
                f"**{officer.display_name}**\nCareer Tokens: `{tokens}`\nWeekly Score: `{weekly}`\n(No detailed history found.)", ephemeral=True)
            return

        view = IntelHistoryView(data["history"], officer)
        await interaction.followup.send(embed=view.make_embed(), view=view, ephemeral=True)

    @app_commands.command(name="metro_active_cases", description="List all active Major Crimes cases currently in the database.")
    async def metro_active_cases(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        cursor = self.metro_cases.find().limit(25)
        cases = await cursor.to_list(length=25)
        
        if not cases:
            await interaction.followup.send("No active cases found.", ephemeral=True)
            return
            
        desc = f"## <:LAPD_Metropolitan:1495867271501975552> | Major Crimes Directory\n{DASHBOARD_DIVIDER}\n"
        for c in cases:
            thread = self.bot.get_channel(c["thread_id"])
            mention = thread.mention if thread else f"`ID: {c['thread_id']}`"
            # Note: We didn't store the OCG name in metro_cases in the previous version, 
            # but we can show Case ID and Link.
            desc += f"🔹 **Case ID{c['case_id']}** — {mention}\n"
            
        embed = discord.Embed(description=desc, color=discord.Color.dark_red())
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="metro_leaderboard", description="Show the top intel point earners for the Metropolitan Unit.")
    async def metro_leaderboard(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        cursor = self.officer_stats.find().sort("weekly_points", -1).limit(10)
        top_officers = await cursor.to_list(length=10)
        
        if not top_officers:
            await interaction.followup.send("No points have been recorded yet.")
            return

        desc = f"## <:LAPD_Metropolitan:1495867271501975552> | Weekly Operational Standings\n{DASHBOARD_DIVIDER}\n"
        for i, data in enumerate(top_officers, 1):
            member = interaction.guild.get_member(data["_id"])
            name = member.mention if member else f"Unknown ({data['_id']})"
            desc += f"**{i}.** {name} — `{data.get('weekly_points', 0)}` pts\n"
        
        embed = discord.Embed(description=desc, color=discord.Color.gold())
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="metro_weekly_stats", description="View division-wide activity statistics.")
    async def metro_weekly_stats(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        last_reset = self.config_cache.get("last_reset")
        if isinstance(last_reset, str):
            last_reset = datetime.datetime.fromisoformat(last_reset)

        aar_count = await self.aar_logs.count_documents({})
        k9_count = await self.k9_logs.count_documents({})
        case_count = await self.case_logs.count_documents({})
        
        # Filter suspect logs count to only include those since the last reset
        intel_query = {}
        if last_reset:
            intel_query["timestamp"] = {"$gte": last_reset}
        intel_count = await self.suspect_logs.count_documents(intel_query)
        
        pipeline = [{"$group": {"_id": None, "total": {"$sum": "$weekly_points"}}}]
        points_res = await self.officer_stats.aggregate(pipeline).to_list(length=1)
        total_points = points_res[0]["total"] if points_res else 0

        embed = discord.Embed(
            title="<:LAPD_Metropolitan:1495867271501975552> Weekly Division Analytics",
            color=discord.Color.blue(),
            description=(
                f"**Points Earned This Cycle:** `{total_points}`\n"
                f"{DASHBOARD_DIVIDER}\n"
                f"🔍 **Weekly Suspect Logs:** `{intel_count}`\n"
                f"📁 **Case Logs:** `{case_count}`\n"
                f"📝 **After Action Reports:** `{aar_count}`\n"
                f"🐕 **K9 Deployments:** `{k9_count}`"
            )
        )
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="metro_shop", description="Redeem your intelligence points for various rewards.")
    async def metro_shop(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        data = await self.officer_stats.find_one({"_id": interaction.user.id})
        points = data.get("intel_points", 0) if data else 0
        
        embed = discord.Embed(
            description=(
                "## 🎉 | Metro Shop Rewards\n"
                f"{DASHBOARD_DIVIDER}\n"
                f"Welcome, Operative. Your hard work has earned you **{points}** Career Intel Tokens. Choose a reward below to redeem them."
            ),
            color=discord.Color.blue()
        )
        await interaction.followup.send(embed=embed, view=ShopView(self, points), ephemeral=True)

    @app_commands.command(name="metro_new_week", description="Purge active logs and prepare for the next operational cycle.")
    async def metro_new_week(self, interaction: discord.Interaction):
        if not self._is_high_command(interaction.user):
            await interaction.response.send_message("❌ Access Denied.", ephemeral=True)
            return

        last_reset = self.config_cache.get("last_reset")
        if isinstance(last_reset, str):
            last_reset = datetime.datetime.fromisoformat(last_reset)

        aar_count = await self.aar_logs.count_documents({})
        k9_count = await self.k9_logs.count_documents({})
        case_count = await self.case_logs.count_documents({})
        
        intel_query = {}
        if last_reset:
            intel_query["timestamp"] = {"$gte": last_reset}
        intel_weekly = await self.suspect_logs.count_documents(intel_query)
        intel_total = await self.suspect_logs.count_documents({})

        embed = discord.Embed(
            title="🚨 Weekly Operations Review",
            description=(
                "Staff must review the following logged activity before purging. Use `/metro_modify_points` "
                "to award additional credit for high-quality reports now.\n\n"
                f"**AARs Logged (Persistent):** `{aar_count}`\n"
                f"**K9 Logs Pending Purge:** `{k9_count}`\n"
                f"**Case Logs (Persistent):** `{case_count}`\n\n"
                f"💡 *Note: `{intel_weekly}` Suspect Logs were added this week. All `{intel_total}` logs are preserved for AI training.*\n\n"
                "**Click below to finalize the purge.** This action cannot be undone."
            ),
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed, view=WeeklyResetView(self))


async def setup(bot):
    await bot.add_cog(Operations(bot))
