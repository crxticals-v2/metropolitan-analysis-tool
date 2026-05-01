"""
raffle.py – Metropolitan Raffle System
========================================
Weekly weighted raffle where officers spend Intel Points on tickets for a 50 Robux prize.

  Ticket pricing:
    1 ticket  →  3 Intel Points
    2 tickets →  5 Intel Points

  The raffle is drawn automatically inside WeeklyResetView.confirm() in operations.py.
  The winner announcement channel is set via /metro_dashboard → Configure Channels → Raffle Winner.
"""

import random
import datetime
import discord
from discord import app_commands
from discord.ext import commands

# ──────────────────────────────────────────────
# CONSTANTS
# ──────────────────────────────────────────────

PRIZE_DESCRIPTION  = "**50 Robux**"
TICKET_EMOJI       = "🎟️"
RAFFLE_EMOJI       = "🏆"
METRO_ICON_URL     = "https://i.imgur.com/qdvbBqe.png"
DIVIDER            = "**━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━**"

TICKET_TIERS = {
    "raffle_1": {"tickets": 1, "cost": 3},
    "raffle_2": {"tickets": 2, "cost": 5},
}


# ──────────────────────────────────────────────
# TICKET PURCHASE CONFIRMATION VIEW
# ──────────────────────────────────────────────

class RaffleConfirmView(discord.ui.View):
    """Shown to the user to confirm their ticket purchase before deducting points."""

    def __init__(self, cog, user: discord.Member, tier_key: str, current_points: int):
        super().__init__(timeout=60)
        self.cog            = cog
        self.user           = user
        self.tier_key       = tier_key
        self.current_points = current_points
        self._handled       = False

    # ── Confirm ──────────────────────────────────────────────────────────
    @discord.ui.button(label="Confirm Purchase", style=discord.ButtonStyle.success, emoji=TICKET_EMOJI)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user.id:
            return await interaction.response.send_message("❌ This is not your shop session.", ephemeral=True)

        if self._handled:
            return await interaction.response.send_message("❌ Already processed.", ephemeral=True)
        self._handled = True

        tier       = TICKET_TIERS[self.tier_key]
        ticket_qty = tier["tickets"]
        cost       = tier["cost"]

        # Re-verify balance (race-condition safe)
        live = await self.cog.officer_stats.find_one({"_id": self.user.id})
        live_pts = live.get("intel_points", 0) if live else 0

        if live_pts < cost:
            await interaction.response.edit_message(
                content=f"❌ You no longer have enough Intel Points (need `{cost}`, have `{live_pts}`).",
                embed=None, view=None
            )
            return

        # Deduct points & award tickets atomically
        now = datetime.datetime.now(datetime.timezone.utc)
        await self.cog.officer_stats.update_one(
            {"_id": self.user.id},
            {
                "$inc": {"intel_points": -cost},
                "$push": {
                    "history": {
                        "reason": f"Raffle Ticket Purchase (×{ticket_qty})",
                        "weekly_gain": 0,
                        "token_gain":  -cost,
                        "timestamp":   now,
                    }
                }
            }
        )
        await self.cog.raffle_tickets.update_one(
            {"_id": self.user.id},
            {"$inc": {"tickets": ticket_qty}},
            upsert=True
        )

        # Read updated totals for the confirmation embed
        updated_stats = await self.cog.officer_stats.find_one({"_id": self.user.id})
        new_pts       = updated_stats.get("intel_points", 0) if updated_stats else 0
        ticket_doc    = await self.cog.raffle_tickets.find_one({"_id": self.user.id})
        total_tickets = ticket_doc.get("tickets", ticket_qty) if ticket_doc else ticket_qty

        embed = discord.Embed(
            description=(
                f"## {TICKET_EMOJI} | Entry Secured\n"
                f"{DIVIDER}\n"
                f"You are officially in this week's draw for {PRIZE_DESCRIPTION}.\n\n"
                f"**Purchased:** `+{ticket_qty}` ticket{'s' if ticket_qty != 1 else ''}\n"
                f"**Your Tickets:** `{total_tickets}` {TICKET_EMOJI}\n"
                f"**Spent:** `-{cost}` Intel Points\n"
                f"**Balance:** `{new_pts}` pts"
            ),
            color=discord.Color.gold()
        )
        pool = await self.cog.get_pool_snapshot()
        embed.add_field(name="Current Pool", value=self.cog.format_pool_value(pool, self.user.id), inline=False)
        embed.add_field(
            name="Draw",
            value="Winner is selected during `/metro_new_week`; each ticket is one weighted entry.",
            inline=False,
        )
        embed.set_thumbnail(url=METRO_ICON_URL)
        embed.set_footer(text="Metropolitan Unit • Weekly Raffle")

        await interaction.response.edit_message(embed=embed, view=None, content=None)

    # ── Cancel ───────────────────────────────────────────────────────────
    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user.id:
            return await interaction.response.send_message("❌ This is not your shop session.", ephemeral=True)
        self._handled = True
        await interaction.response.edit_message(content="Purchase cancelled.", embed=None, view=None)


# ──────────────────────────────────────────────
# COG
# ──────────────────────────────────────────────

class RaffleCog(commands.Cog, name="RaffleCog"):
    """Weekly Intel Point raffle system for the Metropolitan Unit."""

    def __init__(self, bot: commands.Bot):
        self.bot            = bot
        self.raffle_tickets = bot.mongo_client["erlc_database"]["raffle_tickets"]
        self.officer_stats  = bot.mongo_client["erlc_database"]["officer_stats"]
        self.settings       = bot.mongo_client["erlc_database"]["settings"]
        self.config_cache: dict = {}

    async def cog_load(self):
        await self.load_config()

    # ── Config helper (mirrors the pattern in Operations) ─────────────────
    async def load_config(self):
        doc = await self.settings.find_one({"_id": "guild_config"})
        self.config_cache = doc or {}

    async def _resolve_raffle_channel(
        self,
        guild: discord.Guild,
        fallback_interaction: discord.Interaction | None = None
    ) -> discord.TextChannel | None:
        """Return the configured raffle_winner channel, or None if not set."""
        await self.load_config()
        channels = self.config_cache.get("channels", {})
        channel_id = channels.get("raffle_winner")

        if channel_id:
            ch = guild.get_channel(channel_id)
            if ch:
                return ch

        # Graceful fallback: warn staff in DM / ephemeral if possible
        if fallback_interaction:
            try:
                await fallback_interaction.followup.send(
                    "⚠️ **Raffle winner channel is not configured.**\n"
                    "Go to `/metro_dashboard` → *Configure Channels* → *Raffle Winner Announcement* to set it.",
                    ephemeral=True
                )
            except Exception:
                pass
        return None

    async def get_user_ticket_count(self, user_id: int) -> int:
        doc = await self.raffle_tickets.find_one({"_id": user_id})
        return max(0, int(doc.get("tickets", 0))) if doc else 0

    async def get_pool_snapshot(self) -> dict:
        cursor = self.raffle_tickets.find({})
        entries = await cursor.to_list(length=None)

        participants: dict[int, int] = {}
        total_tickets = 0
        for doc in entries:
            count = max(0, int(doc.get("tickets", 0)))
            if count <= 0:
                continue
            uid = int(doc["_id"])
            participants[uid] = participants.get(uid, 0) + count
            total_tickets += count

        return {
            "participants": participants,
            "participant_count": len(participants),
            "total_tickets": total_tickets,
        }

    def format_pool_value(self, pool: dict, user_id: int | None = None) -> str:
        total = pool["total_tickets"]
        participants = pool["participant_count"]
        lines = [
            f"**Total Tickets:** `{total}` {TICKET_EMOJI}",
            f"**Participants:** `{participants}`",
        ]
        if user_id is not None and total:
            user_tickets = pool["participants"].get(user_id, 0)
            odds = user_tickets / total * 100
            lines.append(f"**Your Current Chance:** `{odds:.1f}%`")
        return "\n".join(lines)

    def format_top_entries(self, guild: discord.Guild | None, pool: dict, limit: int = 5) -> str:
        participants = pool["participants"]
        if not participants:
            return "No tickets have been purchased yet."

        lines = []
        sorted_parts = sorted(participants.items(), key=lambda item: item[1], reverse=True)
        for index, (uid, tickets) in enumerate(sorted_parts[:limit], start=1):
            member = guild.get_member(uid) if guild else None
            name = member.mention if member else f"`{uid}`"
            lines.append(f"`#{index}` {name} — **{tickets}** {TICKET_EMOJI}")

        if len(sorted_parts) > limit:
            lines.append(f"*...and {len(sorted_parts) - limit} more entrant{'s' if len(sorted_parts) - limit != 1 else ''}*")
        return "\n".join(lines)

    def build_ticket_status_embed(
        self,
        user: discord.Member,
        tickets: int,
        points: int,
        pool: dict,
        guild: discord.Guild | None = None,
    ) -> discord.Embed:
        total_tickets = pool["total_tickets"]
        odds = (tickets / total_tickets * 100) if total_tickets else 0
        next_best = "1 ticket for 3 pts" if points < 5 else "2 tickets for 5 pts"

        embed = discord.Embed(
            description=(
                f"## {RAFFLE_EMOJI} | Weekly Raffle\n"
                f"{DIVIDER}\n"
                f"**Officer:** {user.mention}\n"
                f"**Prize:** {PRIZE_DESCRIPTION}\n\n"
                f"### {TICKET_EMOJI} Your Entries\n"
                f"**Tickets Held:** `{tickets}`\n"
                f"**Estimated Chance:** `{odds:.1f}%`\n"
                f"**Intel Points:** `{points}`\n\n"
                f"### Pool Status\n"
                f"{self.format_pool_value(pool)}"
            ),
            color=discord.Color.blurple() if tickets else discord.Color.greyple(),
        )
        embed.add_field(
            name="Ticket Tiers",
            value=(
                "`1` ticket: `3` pts\n"
                "`2` tickets: `5` pts\n"
                f"Best available for you right now: **{next_best}**"
            ),
            inline=False,
        )
        embed.add_field(name="Top Entries", value=self.format_top_entries(guild, pool), inline=False)
        embed.add_field(
            name="How It Works",
            value="Tickets reset after the weekly draw. Buy entries from `/metro_shop`.",
            inline=False,
        )
        embed.set_thumbnail(url=METRO_ICON_URL)
        embed.set_footer(text="Metropolitan Unit • Weekly Raffle")
        return embed

    async def show_ticket_status(
        self,
        interaction: discord.Interaction,
        *,
        edit_message: bool = False,
    ):
        stats = await self.officer_stats.find_one({"_id": interaction.user.id})
        points = stats.get("intel_points", 0) if stats else 0
        tickets = await self.get_user_ticket_count(interaction.user.id)
        pool = await self.get_pool_snapshot()
        embed = self.build_ticket_status_embed(interaction.user, tickets, points, pool, interaction.guild)

        if edit_message:
            await interaction.response.edit_message(content=None, embed=embed, view=None)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── Called from ShopView.select_item() in operations.py ──────────────
    async def purchase_tickets(
        self,
        interaction: discord.Interaction,
        shop_view,               # The ShopView instance from operations.py
        tier_key: str,
    ):
        """
        Fast-path ticket purchase called directly from ShopView — no HC approval required.
        Sends a confirmation prompt back to the user before deducting anything.
        """
        tier  = TICKET_TIERS[tier_key]
        cost  = tier["cost"]
        qty   = tier["tickets"]
        pts   = shop_view.points  # cached balance from ShopView.__init__

        if pts < cost:
            return await interaction.response.edit_message(
                content=f"❌ You need **{cost}** Intel Points for this, but you only have **{pts}**.",
                embed=None, view=None
            )

        # Show confirmation embed before committing
        ticket_doc    = await self.raffle_tickets.find_one({"_id": interaction.user.id})
        current_held  = ticket_doc.get("tickets", 0) if ticket_doc else 0

        embed = discord.Embed(
            description=(
                f"## {TICKET_EMOJI} | Confirm Raffle Entry\n"
                f"{DIVIDER}\n"
                f"Confirm purchase of **{qty} raffle ticket{'s' if qty != 1 else ''}** "
                f"for `{cost}` Intel Points.\n\n"
                f"**Balance After:** `{pts - cost}` pts\n"
                f"**Tickets Now:** `{current_held}` {TICKET_EMOJI}\n"
                f"**Tickets After:** `{current_held + qty}` {TICKET_EMOJI}\n"
                f"**Prize:** {PRIZE_DESCRIPTION}"
            ),
            color=discord.Color.blurple()
        )
        pool = await self.get_pool_snapshot()
        projected_total = pool["total_tickets"] + qty
        projected_chance = ((current_held + qty) / projected_total * 100) if projected_total else 0
        embed.add_field(
            name="Projected Odds",
            value=(
                f"Pool after purchase: `{projected_total}` tickets\n"
                f"Your projected chance: `{projected_chance:.1f}%`"
            ),
            inline=False,
        )
        embed.set_thumbnail(url=METRO_ICON_URL)
        embed.set_footer(text="Metropolitan Unit • Weekly Raffle")

        await interaction.response.edit_message(
            embed=embed,
            view=RaffleConfirmView(self, interaction.user, tier_key, pts),
            content=None
        )

    # ── Called from WeeklyResetView.confirm() in operations.py ───────────
    async def run_raffle(
        self,
        guild: discord.Guild,
        interaction: discord.Interaction,
    ):
        """
        Weighted random raffle draw.
        Each ticket gives the officer one entry in the pool — purely random pick,
        not simply "most tickets wins", but more tickets = higher probability.
        Clears all tickets after the draw regardless of outcome.
        """
        # Fetch all entries
        cursor  = self.raffle_tickets.find({})
        entries = await cursor.to_list(length=None)

        raffle_channel = await self._resolve_raffle_channel(guild, interaction)

        # ── No entries edge-case ──────────────────────────────────────────
        async def send_no_entries():
            embed = discord.Embed(
                description=(
                    f"## {TICKET_EMOJI} | Weekly Raffle — No Entries\n"
                    f"{DIVIDER}\n"
                    "No operatives purchased raffle tickets this week.\n"
                    f"The {PRIZE_DESCRIPTION} prize rolls over to next week's draw.\n\n"
                    f"*Buy tickets next week via `/metro_shop`!*"
                ),
                color=discord.Color.greyple()
            )
            embed.set_thumbnail(url=METRO_ICON_URL)
            embed.set_footer(text="Metropolitan Unit • Weekly Raffle")
            if raffle_channel:
                await raffle_channel.send(embed=embed)
            await self.raffle_tickets.delete_many({})

        if not entries:
            await send_no_entries()
            return

        # ── Build weighted pool ───────────────────────────────────────────
        # [user_id, user_id, user_id, ...] — one entry per ticket owned
        pool: list[int] = []
        total_tickets   = 0
        participant_map: dict[int, int] = {}  # user_id → ticket count

        for doc in entries:
            uid   = doc["_id"]
            count = max(0, int(doc.get("tickets", 0)))
            if count <= 0:
                continue
            pool.extend([uid] * count)
            total_tickets           += count
            participant_map[uid]     = count

        if not pool:
            await send_no_entries()
            return

        # ── Draw the winner ───────────────────────────────────────────────
        winner_id  = random.choice(pool)
        winner     = guild.get_member(winner_id)
        winner_str = winner.mention if winner else f"Officer `{winner_id}`"

        # Winner's ticket share for display
        winner_tickets = participant_map[winner_id]
        win_pct = (winner_tickets / total_tickets * 100) if total_tickets else 0

        # ── Participant summary (top 8 by tickets, rest aggregated) ───────
        sorted_parts = sorted(participant_map.items(), key=lambda x: x[1], reverse=True)
        participants_text = ""
        for i, (uid, tkt) in enumerate(sorted_parts[:8]):
            m    = guild.get_member(uid)
            name = m.mention if m else f"`{uid}`"
            crown = " 👑" if uid == winner_id else ""
            participants_text += f"{TICKET_EMOJI} {name} — **{tkt}** ticket{'s' if tkt != 1 else ''}{crown}\n"
        if len(sorted_parts) > 8:
            rest = len(sorted_parts) - 8
            participants_text += f"*...and {rest} more operative{'s' if rest != 1 else ''}*\n"

        # ── Build announcement embed ──────────────────────────────────────
        embed = discord.Embed(
            description=(
                f"## 🏆 | Weekly Raffle — We Have a Winner!\n"
                f"{DIVIDER}\n"
                f"Congratulations to {winner_str}! 🎉\n\n"
                f"**Prize:** {PRIZE_DESCRIPTION}\n"
                f"**Winning Tickets:** `{winner_tickets}` / `{total_tickets}` total "
                f"(`{win_pct:.1f}%` draw chance)\n\n"
                f"{DIVIDER}\n"
                f"### {TICKET_EMOJI} This Week's Participants\n"
                f"{participants_text}"
                f"{DIVIDER}\n"
                "🎟️ *Tickets reset for the new week. Stay active and earn Intel Points "
                "to enter the next draw via `/metro_shop`!*"
            ),
            color=discord.Color.gold()
        )
        embed.set_thumbnail(url=METRO_ICON_URL)
        embed.set_footer(
            text=f"Metropolitan Unit • Weekly Raffle  |  {datetime.datetime.now().strftime('%Y-%m-%d')}"
        )

        # ── Send winner announcement ──────────────────────────────────────
        if raffle_channel:
            content = winner.mention if winner else None
            await raffle_channel.send(content=content, embed=embed)
        else:
            # Fallback: post in wherever the reset interaction was triggered
            try:
                await interaction.followup.send(embed=embed)
            except Exception:
                pass

        # ── Clear all tickets for the new cycle ───────────────────────────
        await self.raffle_tickets.delete_many({})

    @app_commands.command(name="metro_raffle", description="View your weekly raffle tickets and current draw odds.")
    async def metro_raffle(self, interaction: discord.Interaction):
        await self.show_ticket_status(interaction)


# ──────────────────────────────────────────────
# SETUP
# ──────────────────────────────────────────────

async def setup(bot: commands.Bot):
    await bot.add_cog(RaffleCog(bot))
