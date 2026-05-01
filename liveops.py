"""Live operation planning and readiness board components."""

import time
import asyncio
import io
import json
import discord
import datetime
import hashlib
from PIL import Image, ImageDraw
from pathlib import Path

# ── Components v2 is always on — requires discord.py 2.7+ ────────────────────
USE_COMPONENTS_V2 = True
# ─────────────────────────────────────────────────────────────────────────────

# ──────────────────────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────────────────────

METRO_ICON   = "https://i.imgur.com/qdvbBqe.png"
METRO_EMOJI  = "<:LAPD_Metropolitan:1495867271501975552>"
DIVIDER      = "**━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━**"
MAP_PATH     = Path(__file__).parent / "fall_postals.jpg"

# Load erlc_map.json for pixel coordinates — keyed by node ID (e.g. "N-205")
_MAP_DB_PATH = Path(__file__).parent / "erlc_map.json"
_MAP_DB: dict = {}
if _MAP_DB_PATH.exists():
    with open(_MAP_DB_PATH, "r") as _f:
        _raw = json.load(_f)
        # Support both {"nodes": {...}} wrapper and a flat {node_id: {...}} dict
        _MAP_DB = _raw.get("nodes", _raw)

_OVERWATCH = {"Sniper", "Drone Operator", "Stakeout 1", "Stakeout 2"}


def _get_zoomed_map(graph, postal: str) -> io.BytesIO | None:
    if not MAP_PATH.exists():
        print("[MAP CROP] fall_postals.jpg not found")
        return None

    node_id = graph.resolve_target(postal)

    # Normalize to N-#### regardless of what resolve_target returns
    if not node_id or not node_id.startswith("N-"):
        node_id = "N-" + (node_id or postal).split("_")[-1]

    node_data = _MAP_DB.get(node_id)
    if not node_data:
        print(f"[MAP CROP] Node '{node_id}' not found in erlc_map.json")
        return None

    x = node_data.get("x")
    y = node_data.get("y")
    if x is None or y is None:
        print(f"[MAP CROP] Node '{node_id}' has no x/y coordinates in erlc_map.json")
        return None

    try:
        with Image.open(MAP_PATH) as img:
            half_x = 700  # Expanded X for cinematic widescreen embed coverage
            half_y = 220  # Balanced Y height
            img_w, img_h = img.size
            # Clamp the box so we never crop outside the image boundaries
            left   = max(0, x - half_x)
            top    = max(0, y - half_y)
            right  = min(img_w, x + half_x)
            bottom = min(img_h, y + half_y)
            crop = img.crop((left, top, right, bottom))

            # Add tactical multiple circular rings at the target node
            draw = ImageDraw.Draw(crop)
            rx, ry = x - left, y - top
            # Draw three concentric rings for high visibility tactical targeting
            draw.ellipse([rx - 25, ry - 25, rx + 25, ry + 25], outline=(255, 0, 0, 255), width=6)
            draw.ellipse([rx - 40, ry - 40, rx + 40, ry + 40], outline=(255, 0, 0, 180), width=3)
            draw.ellipse([rx - 55, ry - 55, rx + 55, ry + 55], outline=(255, 0, 0, 100), width=2)

            buf = io.BytesIO()
            crop.save(buf, format="PNG")
            buf.seek(0)
            return buf
    except Exception as e:
        print(f"[MAP CROP ERROR] {e}")
        return None

def _group_assignments(assignments: dict) -> dict[str, dict]:
    """Return assignments sorted into labelled team buckets, skipping empty ones."""
    groups = {
        "🎯 Overwatch":       {},
        "🔵 Element Alpha":   {},
        "🔵 Element Bravo":   {},
        "🔵 Element Charlie": {},
    }
    for role, member in assignments.items():
        if role in _OVERWATCH or role.startswith("Stakeout"):
            groups["🎯 Overwatch"][role] = member
        elif role.endswith(" A") or "Alpha" in role:
            groups["🔵 Element Alpha"][role] = member
        elif role.endswith(" B") or "Bravo" in role:
            groups["🔵 Element Bravo"][role] = member
        elif role.endswith(" C") or "Charlie" in role:
            groups["🔵 Element Charlie"][role] = member
        else:
            groups["🎯 Overwatch"][role] = member   # fallback
    return {k: v for k, v in groups.items() if v}


# ── Embed builders ─────────────────────────────────────────────────────────────

def _embed_setup(ic: discord.Member, postal: str, assignments: dict, members: list, start_time: str = "Immediate", target_gang: str = "None", warrant_id: str = None) -> discord.Embed:
    """Ephemeral 'Operation Setup' embed shown while the IC assigns roles."""
    warrant_str = f"**Warrant ID:** `{warrant_id}`\n" if warrant_id else ""
    desc_lines = [
        f"## {METRO_EMOJI} | Operation Tactical Planning",
        DIVIDER,
        f"**Incident Commander:** {ic.mention}",
        f"**Operation Zone:** Postal `{postal}`",
        f"**Target Faction:** `{target_gang}`",
        f"{warrant_str}**Scheduled Start:** `{start_time}`",
        f"**Operative Pool:** `{len(members)}` personnel",
        DIVIDER,
        "",
    ]

    if assignments:
        desc_lines.append(f"### 📋 Strategic Assignments  `{len(assignments)}`")
        for group, roles in _group_assignments(assignments).items():
            desc_lines.append(f"**{group}**")
            for role, member in roles.items():
                desc_lines.append(f"> `{role}` → {member.mention}")
            desc_lines.append("")
    else:
        desc_lines += [
            "### 📋 Strategic Assignments  `0`",
            "*Use the dropdown to begin assigning tactical roles.*",
        ]

    embed = discord.Embed(description="\n".join(desc_lines), color=discord.Color.blue())
    embed.set_thumbnail(url=METRO_ICON)
    embed.set_footer(text="OPERATION SETUP  ·  Select a role, then assign an operative")
    return embed


def _embed_briefing(ic: discord.Member, postal: str, assignments: dict, start_time: str = "Immediate", target_gang: str = "None", warrant_id: str = None) -> discord.Embed:
    """Public-facing operation briefing embed (red = hot)."""
    warrant_str = f"**Warrant ID:** `{warrant_id}`\n" if warrant_id else ""
    desc_lines = [
        f"## {METRO_EMOJI} | LIVE OPERATION BRIEFING",
        DIVIDER,
        f"**Incident Commander:** {ic.mention}",
        f"**Operation Zone:** Postal `{postal}`",
        f"**Target Faction:** `{target_gang}`",
        f"{warrant_str}**Scheduled Start:** `{start_time}`",
        f"**Timestamp:** <t:{int(time.time())}:F>",
        DIVIDER,
        "",
        "### 🚦 Element Assignments",
    ]
    for group, roles in _group_assignments(assignments).items():
        desc_lines.append(f"**{group}**")
        for role, member in roles.items():
            desc_lines.append(f"> `{role}` — {member.mention}")
        desc_lines.append("")

    embed = discord.Embed(description="\n".join(desc_lines), color=discord.Color.red())
    embed.set_thumbnail(url=METRO_ICON)
    embed.set_footer(text="AWAITING UNIT READINESS  ·  All elements must confirm before initiation")
    return embed


def _embed_readiness(ic: discord.Member, postal: str, assignments: dict, states: dict, start_time: str = "Immediate", target_gang: str = "None", image_url: str = None, warrant_id: str = None) -> discord.Embed:
    """Readiness board embed — updates live as IC toggles elements."""
    ready_count = sum(1 for v in states.values() if v)
    total_count  = len(states)
    all_ready    = ready_count == total_count
    
    warrant_str = f"  ·  **Warrant:** `{warrant_id}`" if warrant_id else ""
    status_lines: list[str] = []
    for group, roles in _group_assignments(assignments).items():
        status_lines.append(f"**{group}**")
        for role, member in roles.items():
            dot = "🟢" if states.get(role) else "🔴"
            status_lines.append(f"> {dot} `{role}` — {member.mention}")
        status_lines.append("")

    if all_ready:
        footer_note = "✅ **All elements synchronized — awaiting IC initiation.**"
        color = discord.Color.green()
    else:
        footer_note = f"⏳ **{ready_count} / {total_count} elements confirmed ready.**"
        color = discord.Color.red()

    desc_lines = [
        f"## {METRO_EMOJI} | OPERATIONAL READINESS BOARD",
        DIVIDER,
        f"**Start Time:** `{start_time}`",
        f"**Target:** `{target_gang}`{warrant_str}",
        f"**IC:** {ic.mention}  ·  **Zone:** Postal `{postal}`  ·  <t:{int(time.time())}:R>",
        DIVIDER,
        "",
        "### 🚦 Element Status",
        *status_lines,
        footer_note,
    ]

    embed = discord.Embed(description="\n".join(desc_lines), color=color or discord.Color.red())
    embed.set_thumbnail(url=METRO_ICON)
    if image_url:
        embed.set_image(url=image_url)
    embed.set_footer(text="Only the IC can toggle readiness  ·  All-green unlocks initiation")
    return embed


def _embed_initiated(ic: discord.Member, postal: str, assignments: dict, target_gang: str = "None", image_url: str = None, warrant_id: str = None) -> discord.Embed:
    """Final 'Operation Initiated' embed (gold = go)."""
    warrant_str = f"**Warrant ID:** `{warrant_id}`\n" if warrant_id else ""
    desc_lines = [
        f"## ⚡ | OPERATION INITIATED",
        DIVIDER,
        f"**Incident Commander:** {ic.mention}",
        f"**Operation Zone:** Postal `{postal}`",
        f"**Target Faction:** `{target_gang}`",
        f"{warrant_str}**Initiated:** <t:{int(time.time())}:F>",
        DIVIDER,
        "",
        "### 📋 Final Element Manifest",
    ]
    for group, roles in _group_assignments(assignments).items():
        desc_lines.append(f"**{group}**")
        for role, member in roles.items():
            desc_lines.append(f"> ✅ `{role}` — {member.mention}")
        desc_lines.append("")

    embed = discord.Embed(description="\n".join(desc_lines), color=discord.Color.gold())
    embed.set_thumbnail(url=METRO_ICON)
    if image_url:
        embed.set_image(url=image_url)
    embed.set_footer(
        text=f"Initiated by {ic.display_name}",
        icon_url=ic.display_avatar.url if ic.display_avatar else None,
    )
    return embed


def _classify_termination(reason: str) -> tuple[str, discord.Color, str]:
    """Infer the final operation outcome from the IC's termination note."""
    text = reason.casefold()

    failed_terms = (
        "failed", "failure", "unsuccessful", "lost", "no arrest", "no arrests",
        "suspect escaped", "target escaped", "compromised and failed",
        "not successful", "did not succeed", "didn't succeed", "no success",
        "could not complete", "couldn't complete", "unable to complete",
    )
    aborted_terms = (
        "abort", "aborted", "cancelled", "canceled", "called off", "stood down",
        "stand down", "no longer viable", "leak", "leaked", "compromised",
        "postponed", "rescheduled",
    )
    success_terms = (
        "success", "successful", "completed", "complete", "concluded", "done",
        "secured", "mission accomplished", "target arrested", "suspect arrested",
        "arrest made", "arrests made", "objective met",
    )

    if any(term in text for term in failed_terms):
        return "Failed", discord.Color.red(), "❌"
    if any(term in text for term in aborted_terms):
        return "Aborted", discord.Color.dark_grey(), "🛑"
    if any(term in text for term in success_terms):
        return "Success", discord.Color.green(), "✅"
    return "Terminated", discord.Color.orange(), "⏹️"


def _terminated_report_view(
    ic: discord.Member,
    postal: str,
    assignments: dict,
    reason: str,
    outcome: str,
    color: discord.Color,
    emoji: str,
    target_gang: str = "None",
    start_time: str = "Immediate",
    warrant_id: str = None,
) -> discord.ui.LayoutView:
    """Final Components v2 report that replaces the live readiness board."""
    warrant_str = f"**Warrant ID:** `{warrant_id}`\n" if warrant_id else ""
    desc_lines = [
        f"## {emoji} | OPERATION {outcome.upper()}",
        DIVIDER,
        f"**Incident Commander:** {ic.mention}",
        f"**Operation Zone:** Postal `{postal}`",
        f"**Target Faction:** `{target_gang}`",
        f"{warrant_str}**Scheduled Start:** `{start_time}`",
        f"**Finalized:** <t:{int(time.time())}:F>",
        DIVIDER,
        "",
        "### 📋 Final Element Manifest",
    ]

    for group, roles in _group_assignments(assignments).items():
        desc_lines.append(f"**{group}**")
        for role, member in roles.items():
            desc_lines.append(f"> `{role}` — {member.mention}")
        desc_lines.append("")

    desc_lines += [
        DIVIDER,
        "### 📝 Termination Notes",
        reason,
    ]

    container = discord.ui.Container(accent_colour=color)
    container.add_item(discord.ui.TextDisplay("\n".join(desc_lines)))
    container.add_item(discord.ui.Separator())
    container.add_item(discord.ui.TextDisplay(f"*Closed by {ic.display_name}*"))

    view = discord.ui.LayoutView()
    view.add_item(container)
    return view


# _try_build_v2_readiness removed — layout is now owned by LiveOpReadinessView._rebuild()
# which uses LayoutView; the flag is set automatically by discord.py.


# ──────────────────────────────────────────────────────────────────────────────
# VIEWS
# ──────────────────────────────────────────────────────────────────────────────

class LiveOpAssignmentView(discord.ui.View):
    """
    Ephemeral setup view shown to the IC.
    Provides a role dropdown → member dropdown flow, building assignments
    live with an updating embed, then a Finalize button to post the briefing.
    """

    ROLE_LIST = [
        "Sniper", "Drone Operator", "Stakeout 1", "Stakeout 2",
        "TL Alpha",   "Breacher A", "Driver A", "Negotiator A", "Point A", "Cover A", "Rear A",
        "TL Bravo",   "Breacher B", "Driver B", "Negotiator B", "Point B", "Cover B", "Rear B",
        "TL Charlie", "Breacher C", "Driver C", "Negotiator C", "Point C", "Cover C", "Rear C",
    ]

    def __init__(self, cog, ic: discord.Member, postal: str, members: list[discord.Member], start_time: str = "Immediate", target_gang: str = "None", warrant_id: str = None):
        super().__init__(timeout=600)
        self.cog         = cog
        self.ic          = ic
        self.postal      = postal
        self.start_time  = start_time
        self.target_gang = target_gang
        self.warrant_id  = warrant_id
        self.members     = members
        self.assignments: dict[str, discord.Member] = {}
        self._refresh()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _refresh(self):
        self.clear_items()
        unassigned = [r for r in self.ROLE_LIST if r not in self.assignments]

        role_select = discord.ui.Select(
            placeholder="🎯  Select a role to assign…" if unassigned else "✅  All roles filled",
            options=[discord.SelectOption(label=r, value=r) for r in unassigned][:25],
            disabled=not unassigned,
            row=0,
        )
        role_select.callback = self._role_picked
        self.add_item(role_select)

        finalize = discord.ui.Button(
            label="Finalize & Notify Operatives",
            style=discord.ButtonStyle.primary,
            emoji="📡",
            disabled=not self.assignments,
            row=1,
        )
        finalize.callback = self._finalize
        self.add_item(finalize)

    def _member_select(self, role: str) -> discord.ui.Select:
        return discord.ui.Select(
            placeholder=f"👤  Assign operative to: {role}…",
            options=[
                discord.SelectOption(label=m.display_name, value=str(m.id))
                for m in self.members
            ][:25],
            row=0,
        )

    # ── Callbacks ─────────────────────────────────────────────────────────────

    async def _role_picked(self, interaction: discord.Interaction):
        role = interaction.data["values"][0]

        member_view = discord.ui.View(timeout=120)
        sel = self._member_select(role)

        async def _member_picked(inter: discord.Interaction):
            uid = int(inter.data["values"][0])
            member = discord.utils.get(self.members, id=uid)
            self.assignments[role] = member
            self._refresh()
            embed = _embed_setup(self.ic, self.postal, self.assignments, self.members, self.start_time, self.target_gang, self.warrant_id)
            await inter.response.edit_message(embed=embed, view=self)

        sel.callback = _member_picked
        member_view.add_item(sel)

        # Keep the embed visible while picking
        embed = _embed_setup(self.ic, self.postal, self.assignments, self.members, self.start_time, self.target_gang, self.warrant_id)
        await interaction.response.edit_message(embed=embed, view=member_view)

    async def _finalize(self, interaction: discord.Interaction):
        if not self.assignments:
            return await interaction.response.send_message(
                "❌ Assign at least one role before finalizing.", ephemeral=True
            )

        await interaction.response.defer()
        
        # Save Operation to MongoDB for persistence
        op_id = "0000"
        db_id = None
        try:
            op_data = {
                "ic_id": self.ic.id,
                "postal": self.postal,
                "start_time": self.start_time,
                "target_gang": self.target_gang,
                "warrant_id": self.warrant_id,
                "assignments": {role: m.id for role, m in self.assignments.items()},
                "status": "Planning",
                "timestamp": datetime.datetime.now(datetime.timezone.utc),
                "guild_id": interaction.guild_id
            }
            result = await self.cog.live_ops.insert_one(op_data)
            db_id = result.inserted_id
            op_id = str(result.inserted_id)[-4:].upper()
        except Exception as e:
            print(f"[LIVE OP DB ERROR] {e}")

        # Generate zoomed map crop
        map_file = discord.utils.MISSING
        loop = asyncio.get_running_loop()
        buf = await loop.run_in_executor(None, _get_zoomed_map, self.cog.bot.erlc_graph, self.postal)

        if buf:
            map_file = discord.File(fp=buf, filename="op_map.png")

        # Pre-build manifest string for notifications
        manifest_text = "\n".join([f"> `{role}` — {m.display_name}" for role, m in self.assignments.items()])

        # ── 1. Prepare Readiness Board ────────────────────────────────────────
        # The LiveOpReadinessView Container includes all briefing info + per-assignment
        # status rows, so no separate briefing embed is needed alongside it.
        readiness_view = LiveOpReadinessView(self.cog, self.ic, self.assignments, self.postal, self.start_time, db_id=db_id, target_gang=self.target_gang, warrant_id=self.warrant_id)
        if buf:
            readiness_view._map_buf = buf
            readiness_view.image_url = "attachment://op_map.png"
            readiness_view._rebuild()

        kwargs: dict = dict(
            content=None,
            view=readiness_view,
        )
        if map_file is not discord.utils.MISSING:
            kwargs["file"] = map_file

        # ── 2. Post Operation Readiness Board to configured channel ────────────
        readiness_channel = await self.cog._resolve_output_channel(interaction, "live_ops_readiness")
        if not hasattr(readiness_channel, "send"):
            readiness_channel = interaction.channel

        try:
            readiness_message = await readiness_channel.send(**kwargs)
        except (discord.Forbidden, discord.HTTPException):
            readiness_channel = interaction.channel
            readiness_message = await readiness_channel.send(**kwargs)

        if db_id:
            await self.cog.live_ops.update_one(
                {"_id": db_id},
                {
                    "$set": {
                        "status": "Readiness",
                        "readiness_channel_id": readiness_channel.id,
                        "readiness_message_id": readiness_message.id,
                    }
                }
            )

        channel_label = getattr(readiness_channel, "mention", f"`{readiness_channel}`")
        await interaction.followup.send(
            f"✅ Operational readiness board posted in {channel_label}.",
            ephemeral=True,
        )

        # ── 3. Dispatch Notifications (Background) ─────────────────────────────
        # We run the DM loop in a background task so it doesn't block the IC
        # or prevent the readiness board from appearing if a DM fails.
        async def _dispatch_dms():
            assignments_by_member: dict[int, dict] = {}
            for role, member in self.assignments.items():
                bucket = assignments_by_member.setdefault(member.id, {"member": member, "roles": []})
                bucket["roles"].append(role)

            for data in assignments_by_member.values():
                member = data["member"]
                roles = data["roles"]
                m_hash = hashlib.md5(str(member.id).encode()).hexdigest()[:4].upper()
                discrete_serial = f"METOPERATION-{op_id}-{m_hash}"
                role_lines = "\n".join(f"> `{role}`" for role in roles)
                role_header = "Assigned Roles" if len(roles) > 1 else "Assigned Role"

                try:
                    dm_embed = discord.Embed(
                        description=(
                            f"## {METRO_EMOJI} | CONFIDENTIAL BRIEFING: {self.postal}\n"
                            f"**━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━**\n"
                            f"You have been assigned to a live operation. Your briefing is available here\n"
                            f"{f'**Warrant:** `{self.warrant_id}`' if self.warrant_id else ''}\n"
                            f"**AO:** `{self.postal}` · **Start:** `{self.start_time}`\n\n"
                            f"**Target:** `{self.target_gang}`\n\n"
                            f"### 📋 Strategic Manifest\n{manifest_text}\n\n"
                            f"### {role_header}\n{role_lines}\n\n"
                            f"**Incident Commander:** {self.ic.mention}\n"
                            f"**Convene with your Incident Commander prior to operation initiation.**\n"
                            f"**━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━**\n"
                            f"Review the manifest above. Maintain operational security."
                        ),
                        color=discord.Color.gold(),
                        timestamp=datetime.datetime.now(datetime.timezone.utc)
                    )
                    dm_embed.set_thumbnail(url=METRO_ICON)
                    dm_embed.set_footer(
                        text=f"CLASSIFIED DOCUMENT  ·  SERIAL: {discrete_serial}", 
                        icon_url=METRO_ICON
                    )
                    await member.send(embed=dm_embed)
                except discord.Forbidden:
                    pass # Handle closed DMs silently
                except Exception as e:
                    print(f"[LIVE OP DM ERROR] {member.display_name}: {e}")

        self.cog.bot.loop.create_task(_dispatch_dms())


# ─────────────────────────────────────────────────────────────────────────────

class TerminateOperationModal(discord.ui.Modal):
    reason = discord.ui.TextInput(
        label="Reason for Termination",
        placeholder="Reason for termination (e.g., operation completed, no longer viable, leaks, etc.)",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500
    )

    def __init__(self, view: 'LiveOpReadinessView'):
        super().__init__(title="Terminate Live Operation")
        self.view = view

    async def on_submit(self, interaction: discord.Interaction):
        outcome, color, emoji = _classify_termination(self.reason.value)
        now = datetime.datetime.now(datetime.timezone.utc)

        if self.view.db_id:
            await self.view.cog.live_ops.update_one(
                {"_id": self.view.db_id},
                {
                    "$set": {
                        "status": outcome,
                        "termination_reason": self.reason.value,
                        "terminated_at": now,
                    }
                }
            )

        final_view = _terminated_report_view(
            self.view.ic,
            self.view.postal,
            self.view.assignments,
            self.reason.value,
            outcome,
            color,
            emoji,
            self.view.target_gang,
            self.view.start_time,
            self.view.warrant_id,
        )

        await interaction.response.edit_message(
            content=None,
            embed=None,
            view=final_view,
            attachments=[],
        )


class LiveOpReadinessView(discord.ui.LayoutView):
    """
    Public-facing readiness board — Components v2 (discord.py 2.7+)."""

    def __init__(self, cog, ic: discord.Member, assignments: dict, postal: str, start_time: str = "Immediate", db_id=None, target_gang: str = "None", warrant_id: str = None):
        super().__init__(timeout=None)
        self.cog         = cog
        self.ic          = ic
        self.assignments = assignments
        self.postal      = postal
        self.start_time  = start_time
        self.target_gang = target_gang
        self.warrant_id  = warrant_id
        self.status      = "Readiness"
        self.db_id       = db_id
        self.image_url   = None  # kept for API compatibility; image now sent as attachment
        self.states      = {label: False for label in assignments}
        self._rebuild()
    def set_image(self, *, url: str):
        """Store the attachment URL so _rebuild can embed it via MediaGallery."""
        self.image_url = url

    # ── Internal ──────────────────────────────────────────────────────────────

    def _rebuild(self):
        """Rebuild the entire Components v2 Container from current state."""
        self.clear_items()

        is_active  = self.status == "Active"
        all_ready  = all(self.states.values()) and not is_active
        ready_count = sum(1 for v in self.states.values() if v)
        total_count = len(self.states)

        # ── Accent colour reflects current phase ─────────────────────────────
        if is_active:
            accent = discord.Color.gold()
        elif all_ready:
            accent = discord.Color.green()
        else:
            accent = discord.Color.red()

        container = discord.ui.Container(accent_colour=accent)

        # ── Header ───────────────────────────────────────────────────────────
        warrant_str = f"  ·  **Warrant:** `{self.warrant_id}`" if self.warrant_id else ""
        if is_active:
            phase_line = f"## ⚡  OPERATION ACTIVE — Postal `{self.postal}`"
        elif all_ready:
            phase_line = f"## ✅  ALL ELEMENTS GREEN — Postal `{self.postal}`"
        else:
            phase_line = f"## {METRO_EMOJI}  OPERATIONAL READINESS BOARD — Postal `{self.postal}`"

        container.add_item(discord.ui.TextDisplay(
            f"{phase_line}\n"
            f"{DIVIDER}\n"
            f"**IC:** {self.ic.mention}  ·  "
            f"**Target:** `{self.target_gang}`{warrant_str}  ·  "
            f"**Start:** `{self.start_time}`  ·  "
            f"<t:{int(time.time())}:R>"
        ))
        container.add_item(discord.ui.Separator())

        # ── Tactical map ──────────────────────────────────────────────────────
        if self.image_url:
            container.add_item(discord.ui.MediaGallery(
                discord.MediaGalleryItem(media=discord.UnfurledMediaItem(url=self.image_url))
            ))
            container.add_item(discord.ui.Separator())

        container.add_item(discord.ui.TextDisplay("### 🚦 Element Status"))

        # ── Per-assignment: Section with button accessory ─────────────────────
        items_list = list(self.assignments.items())
        for i, (label, member) in enumerate(items_list):
            ready = self.states[label]

            btn = discord.ui.Button(
                label="🟢 Ready" if ready else "🔴 Not Ready",
                style=discord.ButtonStyle.success if ready else discord.ButtonStyle.danger,
                custom_id=f"ready_{label}",
            )
            btn.callback = self._make_toggle(label)

            section = discord.ui.Section(
                discord.ui.TextDisplay(f"**{label}** | {member.mention}"),
                accessory=btn,
            )
            container.add_item(section)

            if i < len(items_list) - 1:
                container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))

        # ── Summary line ─────────────────────────────────────────────────────
        container.add_item(discord.ui.Separator())
        if is_active:
            container.add_item(discord.ui.TextDisplay(
                "⚡  **Operation is underway.  IC may terminate at any time.**"
            ))
        elif all_ready:
            container.add_item(discord.ui.TextDisplay(
                "✅  **All elements synchronized — awaiting IC initiation command.**"
            ))
        else:
            container.add_item(discord.ui.TextDisplay(
                f"⏳  **{ready_count} / {total_count} elements confirmed ready.**"
            ))

        # ── Control row: Abort (always) + Initiate (all-ready only) ──────────
        container.add_item(discord.ui.Separator())

        terminate_btn = discord.ui.Button(
            label="⏹️  TERMINATE OPERATION",
            style=discord.ButtonStyle.secondary,
            custom_id="terminate_op",
        )
        terminate_btn.callback = self._terminate

        control_row = discord.ui.ActionRow(terminate_btn)

        if all_ready:
            initiate_btn = discord.ui.Button(
                label="⚡  INITIATE OPERATION",
                style=discord.ButtonStyle.primary,
                custom_id="initiate",
            )
            initiate_btn.callback = self._initiate
            control_row.add_item(initiate_btn)

        container.add_item(control_row)

        # ── Register the whole container with the View ────────────────────────
        self.add_item(container)

    async def _terminate(self, interaction: discord.Interaction):
        if interaction.user.id != self.ic.id:
            return await interaction.response.send_message("❌ Only the Incident Commander can terminate the operation.", ephemeral=True)
        
        await interaction.response.send_modal(TerminateOperationModal(self))

    def _make_toggle(self, label: str):
        async def _toggle(interaction: discord.Interaction):
            if interaction.user.id != self.ic.id:
                return await interaction.response.send_message(
                    "❌ Only the Incident Commander can toggle readiness.", ephemeral=True
                )
            self.states[label] = not self.states[label]
            self._rebuild()
            await self._update_message(interaction)
        return _toggle

    async def _update_message(self, interaction: discord.Interaction):
        """Refresh the readiness board in-place using Components v2."""
        await interaction.response.edit_message(
            content=None,
            embed=None,
            view=self,
        )

    # ── Initiate ──────────────────────────────────────────────────────────────

    async def _initiate(self, interaction: discord.Interaction):
        if interaction.user.id != self.ic.id:
            return await interaction.response.send_message(
                "❌ Only the Incident Commander can initiate.", ephemeral=True
            )

        if self.db_id:
            await self.cog.live_ops.update_one(
                {"_id": self.db_id},
                {"$set": {"status": "Active", "initiated_at": datetime.datetime.now(datetime.timezone.utc)}}
            )

        self.status = "Active"
        self._rebuild()  # Rebuilds with gold accent + removes initiate button; abort remains

        await interaction.response.edit_message(
            content=None,
            embed=None,
            view=self,
        )
