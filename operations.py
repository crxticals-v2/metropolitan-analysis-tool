"""
cogs/operations.py

Metropolitan Division administrative & operational commands:
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

import time
import datetime
import discord
from discord import app_commands
from discord.ext import commands


# ──────────────────────────────────────────────
# MODALS
# ──────────────────────────────────────────────

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
            "## **<:LAPD_Metropolitan:1495867271501975552>︱ Training Results**\n"
            "**━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━**\n\n"
            f"**Trainee:** {self.trainee.mention}\n\n"
            f"**Field Training Officer:** {self.host.mention}\n\n"
            f"**Co-Host:** {self.co_host.mention if self.co_host else 'None'}\n\n"
            "**Your Training Results:**\n"
            f"**SECT. I | Firearms Exercise:** {score1}/10\n"
            f"**SECT. II | Stealth/Tactical:** {score2}/10\n"
            f"**SECT. III | Specialist Protection:** {score3}/10\n"
            f"**Overall Score:** {total}/30\n"
            f"**Outcome:** {self.outcome}\n\n"
            "**━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━**\n\n"
            "**Notes:**\n"
            f"> {self.notes}\n\n"
            "**What's Next?**\n"
            "If you passed, congratulations! You are now one of us! You will be roled "
            "shortly and get access to the full division resources.\n"
            "If you failed, do not be discouraged. You may request training anytime.\n"
        )

        embed = discord.Embed(description=desc, color=discord.Color.blue())
        embed.set_thumbnail(url="https://i.imgur.com/qdvbBqe.png")
        embed.set_footer(
            text=f"Issued by {self.host.display_name}",
            icon_url=self.host.display_avatar.url,
        )

        channel = self._resolve_output_channel(interaction, "metro_log_training")
        await channel.send(embed=embed)
        await interaction.response.send_message(
            "✅ Training log has been posted successfully.", ephemeral=True
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
            description=self.announcement.value,
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

        channel = self._resolve_output_channel(interaction, "metro_announcement")
        await channel.send(content=content, embed=embed)
        await interaction.response.send_message(
            "✅ Announcement sent.", ephemeral=True
        )


# ──────────────────────────────────────────────
# COG
# ──────────────────────────────────────────────

class Operations(commands.Cog):
    """Metropolitan Division administrative commands."""

    def __init__(self, bot):
        self.bot = bot
        self.channel_map = {
            "metro_log_training": None,
            "metro_promote": None,
            "metro_announcement": None,
            "metro_infract": None,
            "metro_mass_shift": None,
            "host_metro_training": None,
            "metro_openings": None,
            "request_metro": None,
        }
    def _resolve_output_channel(self, interaction: discord.Interaction, key: str):
        channel_id = self.channel_map.get(key)

        if channel_id:
            channel = self.bot.get_channel(channel_id)
            if channel:
                if interaction.channel and interaction.channel.id == channel.id:
                    return interaction.channel
                return channel

        return interaction.channel

    # ------------------------------------------------------------------ #
    # /metro_log_training                                                  #
    # ------------------------------------------------------------------ #

    @app_commands.command(
        name="metro_log_training",
        description="Log results for a Metropolitan Division training session.",
    )
    async def metro_log_training(
        self,
        interaction: discord.Interaction,
        trainee: discord.Member,
        outcome: str,
        notes: str,
        co_host: discord.Member = None,
    ):
        await interaction.response.send_modal(
            MetroTrainingModal(
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
        desc = (
            "## **<:LAPD_Metropolitan:1495867271501975552>︱ Metropolitan Promotion!**\n"
            "**━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━**\n\n"
            f"**Metro Operative:** {officer.mention}\n\n"
            f"**Old Rank:** {previous_rank}\n\n"
            f"**New Rank:** {new_rank}\n\n"
            f"**Notes:** {notes}\n\n"
            f"**Signed:** {signed}\n\n"
            "**━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━**"
        )

        embed = discord.Embed(description=desc, color=discord.Color.blue())
        embed.set_thumbnail(url="https://i.imgur.com/qdvbBqe.png")
        embed.set_footer(
            text=f"Issued by {interaction.user.display_name}",
            icon_url=(
                interaction.user.display_avatar.url
                if interaction.user.display_avatar
                else None
            ),
        )

        channel = self._resolve_output_channel(interaction, "metro_promote")
        await channel.send(content=officer.mention, embed=embed)
        await interaction.response.send_message(
            "✅ Promotion successfully logged!", ephemeral=True
        )

    # ------------------------------------------------------------------ #
    # /metro_announcement                                                  #
    # ------------------------------------------------------------------ #

    @app_commands.command(
        name="metro_announcement",
        description="Send a Metropolitan Division announcement.",
    )
    @app_commands.describe(ping_type="Choose whether to ping the division or not")
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
            interaction.guild.roles, name="Metropolitan Division"
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
        desc = (
            "## **<:LAPD_Metropolitan:1495867271501975552>︱ Metro Infraction**\n"
            "**━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━**\n\n"
            f"**Officer:** {officer.mention}\n\n"
            f"**Punishment:** {punishment}\n\n"
            f"**Reason:** {reason}\n\n"
            f"**Appealable:** {appealable}\n\n"
            f"**Signed:** {signed}\n\n"
            "**━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━**"
        )

        embed = discord.Embed(description=desc, color=discord.Color.red())
        embed.set_thumbnail(url="https://i.imgur.com/qdvbBqe.png")
        embed.set_footer(
            text=f"Issued by {interaction.user.display_name}",
            icon_url=(
                interaction.user.display_avatar.url
                if interaction.user.display_avatar
                else None
            ),
        )

        channel = self._resolve_output_channel(interaction, "metro_infract")
        await channel.send(content=officer.mention, embed=embed)
        await interaction.response.send_message(
            "✅ Infraction has been posted successfully.", ephemeral=True
        )

    # ------------------------------------------------------------------ #
    # /metro_mass_shift                                                    #
    # ------------------------------------------------------------------ #

    @app_commands.command(
        name="metro_mass_shift",
        description="Announce a Metropolitan Division mass shift.",
    )
    async def metro_mass_shift(
        self,
        interaction: discord.Interaction,
        co_host: discord.Member = None,
    ):
        metro_role = discord.utils.get(
            interaction.guild.roles, name="Metropolitan Division"
        )
        if not metro_role:
            await interaction.response.send_message(
                "Metropolitan Division role not found.", ephemeral=True
            )
            return

        host = interaction.user
        desc = (
            "## <:LAPD_Metropolitan:1495867271501975552> ︱ Metro Mass Shift\n"
            "**━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━**\n"
            f"**Hosted By:** {host.mention}\n\n"
            f"**Co-Host:** {co_host.mention if co_host else 'None'}\n\n"
            "**━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━**\n\n"
            "Metropolitan Operatives are needed in-game. Join up people!\n"
            "**Reactions:**\n✅ = Coming\n❔ = Maybe\n❌ = Unable\n"
        )

        embed = discord.Embed(description=desc, color=discord.Color.red())
        embed.set_thumbnail(url="https://i.imgur.com/qdvbBqe.png")
        embed.set_footer(
            text=f"Issued by {host.display_name}",
            icon_url=host.display_avatar.url if host.display_avatar else None,
        )

        await interaction.response.send_message(
            content="✅ Mass Shift Issued", ephemeral=True
        )
        channel = self._resolve_output_channel(interaction, "metro_mass_shift")
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
        description="Host a Metropolitan Division training session.",
    )
    async def host_metro_training(
        self,
        interaction: discord.Interaction,
        co_host: discord.Member = None,
        start_time: str = "TBD",
    ):
        ping_role = discord.utils.get(
            interaction.guild.roles, name="[𝐌𝐃] Awaiting Training Ping"
        )
        if not ping_role:
            await interaction.response.send_message(
                "Metropolitan Division role not found.", ephemeral=True
            )
            return

        host = interaction.user
        desc = (
            "## 📙 | Metropolitan Entry Training\n"
            "**━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━**\n\n"
            f"**Host:** {host.mention}\n\n"
            f"**Co-Host:** {co_host.mention if co_host else 'N/A'}\n\n"
            f"**Starting Time:** {start_time}\n\n"
            "**Weaponry Trainings** are hands-on trainings in which you, the trainee, "
            "undergo several scenarios designed to evaluate your performance and future "
            "in the Metropolitan Division.\nIf you are a trainer, contact the host to "
            "join as a co-host!\n"
            "This training consists of:\n"
            "• Shooting Exercise\n"
            "• Undercover (UC) Exercise\n"
            "• Protection Detail Exercise\n\n"
            "**━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━**\n"
        )

        embed = discord.Embed(description=desc, color=discord.Color.blue())
        embed.set_thumbnail(url="https://i.imgur.com/qdvbBqe.png")
        embed.set_footer(
            text=f"Announced by {host.display_name}",
            icon_url=host.display_avatar.url if host.display_avatar else None,
        )

        await interaction.response.send_message(
            content="✅ Host training issued.", ephemeral=True
        )
        channel = self._resolve_output_channel(interaction, "host_metro_training")
        msg = await channel.send(content=ping_role.mention, embed=embed)

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
        description="Display current roster and openings for Metro Division ranks.",
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
            ("     [MD] Directorate     ", [
                ("Metro Director",         1),
                ("Metro Deputy Director",  4),
            ]),
            (" [MD] Command Inspector General ", [
                ("Metro Detective Chief Inspector", 4),
                ("Metro Chief Inspector",           4),
            ]),
            ("[MD] General Supervisory Staff", [
                ("Metro Supervisory Sergeant", 5),
            ]),
            ("  [MCS] Major Crimes Detectives  ", [
                ("Metro Senior Detective", 7),
                ("Metro Junior Detective", 50),
            ]),
            ("   [MD] B/C Platoon Operatives  ", [
                ("Metro Senior Officer", 50),
                ("Metro Junior Officer", 50),
            ]),
            ("[MD] Probationary Rank Openings", [
                ("Metro Probationary Officer", 50),
            ]),
        ]

        seal          = "<:LAPD_Metropolitan:1495867271501975552>"
        divider       = "**━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━**"
        embed_color   = discord.Color.from_rgb(5, 164, 232)
        embed_list    = []

        # title embed
        embed_list.append(
            discord.Embed(
                description=f"# {seal} **Metropolitan Division Openings**\n{divider}\n\n",
                color=embed_color,
            )
        )

        for group_name, ranks in rank_groups:
            desc = f"## {seal} **{group_name}** {seal}\n"

            for rank_name, quota in ranks:
                role    = discord.utils.get(guild.roles, name=rank_name)
                members = role.members if role else []
                count   = len(members)

                desc += f"{divider}\n**{rank_name}**\n{divider}\n"
                if not members:
                    desc += "• No officers currently hold this rank.\n"
                else:
                    desc += "\n".join(f"• {m.mention}" for m in members) + "\n"

                desc += (
                    f"→ **Closed Spots:** {count}/{quota}\n"
                    f"→ **Open Spots:** {max(0, quota - count)}/{quota}\n\n"
                )

            desc += divider
            embed_list.append(
                discord.Embed(description=desc, color=embed_color)
            )
        await interaction.response.send_message("✅ Openings have been updated successfully.", ephemeral=True)
        channel = self._resolve_output_channel(interaction, "metro_openings")
        await channel.send(embeds=embed_list)
        

    # ------------------------------------------------------------------ #
    # /metro_after_action                                                 #
    # ------------------------------------------------------------------ #

    @app_commands.command(
        name="metro_after_action",
        description="Create a Metropolitan After Action Report.",
    )
    async def metro_after_action(
        self,
        interaction: discord.Interaction,
        officers: str,
        patrol_area: str,
        time_observed: str,
        suspicious_activity: str,
        actions_taken: str,
        additional_notes: str = None,
        suspect_gender: str = None,
        clothing: str = None,
        vehicle: str = None,
        direction_of_travel: str = None,
    ):
        unix_time = None
        try:
            now = datetime.datetime.now()
            parsed = datetime.datetime.strptime(time_observed, "%H:%M")
            combined = now.replace(hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0)
            unix_time = int(combined.timestamp())
        except Exception:
            unix_time = None

        suspect_gender = suspect_gender if suspect_gender else "N/A"
        clothing = clothing if clothing else "N/A"
        vehicle = vehicle if vehicle else "N/A"
        direction_of_travel = direction_of_travel if direction_of_travel else "N/A"

        time_display = f"<t:{unix_time}:F>" if unix_time else "N/A"

        metro_role = discord.utils.get(interaction.guild.roles, name="Metro Chief Inspector")

        desc = (
            "## <:LAPD_Metropolitan:1495867271501975552> | Metropolitan After Action Report\n"
            "**━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━**\n\n"
            f"**Officer(s):** {officers}\n"
            f"**Patrol Area:** {patrol_area}\n"
            f"**Time Observed:** {time_display}\n\n"
            "**Suspicious Activity Observed:**\n"
            f"> {suspicious_activity}\n\n"
            "**Suspect Description:**\n"
            f"- Gender: {suspect_gender}\n"
            f"- Clothing: {clothing}\n"
            f"- Vehicle: {vehicle}\n"
            f"- Direction of Travel: {direction_of_travel}\n\n"
            "**Actions Taken:**\n"
            f"> {actions_taken}\n\n"
            "**Additional Notes:**\n"
            f"> {additional_notes}\n\n"
            "**Signed,**\n"
            f"{interaction.user.mention} - <:LAPD_Metropolitan:1495867271501975552> | Metro Operative\n\n"
            "**━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━**"
        )

        embed = discord.Embed(description=desc, color=discord.Color.dark_blue())
        embed.set_footer(
            text=f"Issued by {interaction.user.display_name}",
            icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None,
        )

        await interaction.channel.send(
            content=metro_role.mention if metro_role else None,
            embed=embed,
        )

        await interaction.response.send_message(
            "✅ After Action Report posted.", ephemeral=True
        )

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
        now        = time.time()
        guild_id   = interaction.guild.id
        cooldown   = self.bot.request_metro_cooldowns
        last_used  = cooldown.get(guild_id)

        # 12-hour server cooldown
        if last_used and (now - last_used) < 43_200:
            remaining = int(43_200 - (now - last_used))
            hours     = remaining // 3600
            minutes   = (remaining % 3600) // 60
            await interaction.response.send_message(
                f"⏳ Command on cooldown for this server. "
                f"Try again in {hours}h {minutes}m.",
                ephemeral=True,
            )
            return

        cooldown[guild_id] = now

        guild      = interaction.guild
        metro_role = discord.utils.get(guild.roles, name="Metropolitan Division")
        swat_role  = discord.utils.get(
            guild.roles, name="Special Weapons and Tactics Team"
        )

        if not metro_role or not swat_role:
            await interaction.response.send_message(
                "No valid response roles found.", ephemeral=True
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

        await interaction.response.send_message(
            "✅ Request sent.", ephemeral=True
        )
        channel = self._resolve_output_channel(interaction, "request_metro")
        await channel.send(
            content=f"{metro_role.mention} {swat_role.mention}",
            embed=embed,
        )

    # ------------------------------------------------------------------ #
    # /k9_deploy                                                           #
    # ------------------------------------------------------------------ #

    @app_commands.command(
        name="k9_deploy",
        description="Log a K9 deployment for Metropolitan Division.",
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
        await interaction.response.defer()

        evidence_text = evidence.url if evidence else "None"
        desc = (
            "### <:LAPD_Metropolitan:1495867271501975552>  | Metropolitan K-Platoon Deployment Log\n"
            "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
            f"> **Handler Name:** {handler_name}\n"
            f"> **K9 Name:** {k9_name}\n"
            f"> **Reason for Deployment:** {reason}\n"
            f"> **Result:** {result}\n"
            f"> **Evidence:** {evidence_text}\n"
            "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬"
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

        await interaction.channel.send(embed=embed)
        await interaction.followup.send(
            "✅ K9 deployment logged.", ephemeral=True
        )


async def setup(bot):
    await bot.add_cog(Operations(bot))
