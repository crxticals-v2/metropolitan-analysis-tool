"""
REDESIGN: /metro_start_live — Live Operation Components
========================================================
Drop these replacements into operations.py:

  1. Place the helper functions + class block below the "LIVE OPERATION COMPONENTS"
     section header (replacing the old LiveOpAssignmentView & LiveOpReadinessView).

  2. Replace the metro_start_live command body inside the Operations cog.

Components v2 note
------------------
The LiveOpReadinessView uses Components v2 (discord.py 2.5 / API flag 1<<15).
If your installed version of discord.py does NOT expose discord.ui.Container or
discord.MessageFlags(is_components_v2=True), set USE_COMPONENTS_V2 = False and
the view falls back gracefully to a standard embed.
"""

import time
import asyncio
import io
import json
import discord
import datetime
import hashlib
from discord import app_commands
from PIL import Image, ImageDraw
from pathlib import Path

# ── Toggle this based on your discord.py version ──────────────────────────────
USE_COMPONENTS_V2 = False   # requires discord.py 2.5+ for Container / TextDisplay
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
        print("[MAP CROP] fall_postals.png not found")
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
            half_x = 500  # Expanded X for cinematic widescreen embed coverage
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

def _embed_setup(ic: discord.Member, postal: str, assignments: dict, members: list, start_time: str = "Immediate", target_gang: str = "None") -> discord.Embed:
    """Ephemeral 'Operation Setup' embed shown while the IC assigns roles."""
    desc_lines = [
        f"## {METRO_EMOJI} | Operation Tactical Planning",
        DIVIDER,
        f"**Incident Commander:** {ic.mention}",
        f"**Operation Zone:** Postal `{postal}`",
        f"**Target Faction:** `{target_gang}`",
        f"**Scheduled Start:** `{start_time}`",
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


def _embed_briefing(ic: discord.Member, postal: str, assignments: dict, start_time: str = "Immediate", target_gang: str = "None") -> discord.Embed:
    """Public-facing operation briefing embed (red = hot)."""
    desc_lines = [
        f"## {METRO_EMOJI} | LIVE OPERATION BRIEFING",
        DIVIDER,
        f"**Incident Commander:** {ic.mention}",
        f"**Operation Zone:** Postal `{postal}`",
        f"**Target Faction:** `{target_gang}`",
        f"**Scheduled Start:** `{start_time}`",
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


def _embed_readiness(ic: discord.Member, postal: str, assignments: dict, states: dict, start_time: str = "Immediate", target_gang: str = "None", image_url: str = None) -> discord.Embed:
    """Readiness board embed — updates live as IC toggles elements."""
    ready_count = sum(1 for v in states.values() if v)
    total_count  = len(states)
    all_ready    = ready_count == total_count
    
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
        f"**Target:** `{target_gang}`",
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


def _embed_initiated(ic: discord.Member, postal: str, assignments: dict, target_gang: str = "None", image_url: str = None) -> discord.Embed:
    """Final 'Operation Initiated' embed (gold = go)."""
    desc_lines = [
        f"## ⚡ | OPERATION INITIATED",
        DIVIDER,
        f"**Incident Commander:** {ic.mention}",
        f"**Operation Zone:** Postal `{postal}`",
        f"**Target Faction:** `{target_gang}`",
        f"**Initiated:** <t:{int(time.time())}:F>",
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


# ── Components v2 helpers (discord.py 2.5+) ───────────────────────────────────

def _try_build_v2_readiness(ic: discord.Member, postal: str, assignments: dict, states: dict):
    """
    Builds a Components v2 Container for the readiness board.
    Returns (container, flags) on success, or (None, None) if v2 is unavailable.

    In discord.py 2.5+ you can pass the returned container directly:
        await interaction.response.edit_message(components=[container], flags=flags, view=view)
    """
    if not USE_COMPONENTS_V2:
        return None, None
    try:
        ready_count = sum(1 for v in states.values() if v)
        total_count  = len(states)
        all_ready    = ready_count == total_count

        accent = discord.Color.green() if all_ready else discord.Color.red()
        container = discord.ui.Container(accent_colour=accent)

        container.add_item(discord.ui.TextDisplay(
            f"## {METRO_EMOJI}  UNIT READINESS STATUS\n"
            f"**IC:** {ic.mention}  ·  **Zone:** Postal `{postal}`  ·  <t:{int(time.time())}:R>"
        ))
        container.add_item(discord.ui.Separator())

        for group, roles in _group_assignments(assignments).items():
            container.add_item(discord.ui.TextDisplay(f"### {group}"))
            for role, member in roles.items():
                dot = "🟢" if states.get(role) else "🔴"
                section = discord.ui.Section(
                    discord.ui.TextDisplay(f"{dot}  **{role}**  —  {member.mention}")
                )
                container.add_item(section)

        container.add_item(discord.ui.Separator())
        if all_ready:
            container.add_item(discord.ui.TextDisplay(
                "✅  **All elements are green — awaiting IC command to initiate.**"
            ))
        else:
            container.add_item(discord.ui.TextDisplay(
                f"⏳  **{ready_count} / {total_count} elements confirmed ready.**"
            ))

        flags = discord.MessageFlags(is_components_v2=True)
        return container, flags

    except (AttributeError, TypeError):
        # discord.py version doesn't expose v2 classes — fall back silently
        return None, None


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

    def __init__(self, cog, ic: discord.Member, postal: str, members: list[discord.Member], start_time: str = "Immediate", target_gang: str = "None"):
        super().__init__(timeout=600)
        self.cog         = cog
        self.ic          = ic
        self.postal      = postal
        self.start_time  = start_time
        self.target_gang = target_gang
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
            embed = _embed_setup(self.ic, self.postal, self.assignments, self.members, self.start_time, self.target_gang)
            await inter.response.edit_message(embed=embed, view=self)

        sel.callback = _member_picked
        member_view.add_item(sel)

        # Keep the embed visible while picking
        embed = _embed_setup(self.ic, self.postal, self.assignments, self.members, self.start_time, self.target_gang)
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

        briefing = _embed_briefing(self.ic, self.postal, self.assignments, self.start_time, self.target_gang)
        if map_file is not discord.utils.MISSING:
            briefing.set_image(url="attachment://op_map.png")

        # Pre-build manifest string for notifications
        manifest_text = "\n".join([f"> `{role}` — {m.display_name}" for role, m in self.assignments.items()])

        # ── 1. Prepare Readiness Board ────────────────────────────────────────
        readiness_view = LiveOpReadinessView(self.cog, self.ic, self.assignments, self.postal, self.start_time, db_id=db_id, target_gang=self.target_gang)
        if map_file is not discord.utils.MISSING:
            readiness_view.set_image(url="attachment://op_map.png")

        kwargs: dict = dict(
            content=(
                f"{METRO_EMOJI} **LIVE OPERATION CHANNEL OPEN**  ·  "
                f"IC: {self.ic.mention}  ·  Zone: Postal `{self.postal}`"
            ),
            embed=briefing,
            view=readiness_view,
        )
        if map_file is not discord.utils.MISSING:
            kwargs["file"] = map_file

        # ── 2. Post Operation Briefing to Channel ─────────────────────────────
        await interaction.followup.send(**kwargs)

        # ── 3. Dispatch Notifications (Background) ─────────────────────────────
        # We run the DM loop in a background task so it doesn't block the IC
        # or prevent the readiness board from appearing if a DM fails.
        async def _dispatch_dms():
            for role, member in self.assignments.items():
                m_hash = hashlib.md5(str(member.id).encode()).hexdigest()[:4].upper()
                discrete_serial = f"METOPERATION-{op_id}-{m_hash}"

                try:
                    dm_embed = discord.Embed(
                        description=(
                            f"## 🚨 {METRO_EMOJI} | CONFIDENTIAL BRIEFING: {self.postal}\n"
                            f"**━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━**"
                            f"You have been assinged to a live operation.\n"
                            f"**AO:** `{self.postal}`  ·  **Start:** `{self.start_time}`\n\n"
                            f"**Target:** `{self.target_gang}`\n\n"
                            f"### 📋 Strategic Manifest\n{manifest_text}\n\n"
                            f"**Your Assigned Role:** `{role}`\n\n"
                            f"**Incident Commander:** {self.ic.mention}\n"
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

class AbortReasonModal(discord.ui.Modal):
    reason = discord.ui.TextInput(
        label="Reason for Abortion",
        placeholder="Provide the reason for standing down the operation...",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500
    )

    def __init__(self, view: 'LiveOpReadinessView'):
        super().__init__(title="Abort Live Operation")
        self.view = view

    async def on_submit(self, interaction: discord.Interaction):
        await self.view.cog.live_ops.update_one(
            {"_id": self.view.db_id},
            {"$set": {"status": "Aborted", "abort_reason": self.reason.value, "aborted_at": datetime.datetime.now(datetime.timezone.utc)}}
        )
        
        embed = discord.Embed(
            title="🛑 OPERATION ABORTED",
            description=f"The operation at Postal `{self.view.postal}` has been cancelled by {self.view.ic.mention}.\n\n**Reason:** {self.reason.value}",
            color=discord.Color.dark_grey()
        )
        await interaction.response.edit_message(content=None, embed=embed, view=None, attachments=[])


class LiveOpReadinessView(discord.ui.View):
    """
    Public-facing readiness board.
    IC toggles each element green/red; when all green the INITIATE button appears.

    Attempts Components v2 for the status display (discord.py 2.5+),
    falls back to a standard embed automatically if v2 is unavailable.
    """

    def __init__(self, cog, ic: discord.Member, assignments: dict, postal: str, start_time: str = "Immediate", db_id=None, target_gang: str = "None"):
        super().__init__(timeout=None)
        self.cog         = cog
        self.ic          = ic
        self.assignments = assignments
        self.postal      = postal
        self.start_time  = start_time
        self.target_gang = target_gang
        self.status      = "Planning" # Initial status
        self.db_id       = db_id
        self.image_url   = None
        self.states      = {label: False for label in assignments}
        self._rebuild()

    def set_image(self, *, url: str):
        self.image_url = url

    # ── Internal ──────────────────────────────────────────────────────────────

    def _rebuild(self):
        self.clear_items()
        all_ready = all(self.states.values()) and self.status != "Active" # Can't be "ready" if already active

        for i, (label, member) in enumerate(self.assignments.items()):
            ready = self.states[label]
            btn = discord.ui.Button(
                label=f"{'✅' if ready else '🔴'}  {label}: {member.display_name}",
                style=discord.ButtonStyle.success if ready else discord.ButtonStyle.danger,
                custom_id=f"ready_{label}",
                row=min(i // 5, 3),
            )
            btn.callback = self._make_toggle(label)
            self.add_item(btn)

        # Abort button available to the IC at all times
        abort = discord.ui.Button(
            label="✖  ABORT OPERATION",
            style=discord.ButtonStyle.secondary,
            custom_id="abort_op",
            row=4
        )
        abort.callback = self._abort
        self.add_item(abort)

        if all_ready and self.status != "Active": # Only show initiate button if not yet active
            initiate = discord.ui.Button(
                label="⚡  INITIATE OPERATION",
                style=discord.ButtonStyle.primary,
                custom_id="initiate",
                row=4,
            )
            initiate.callback = self._initiate
            self.add_item(initiate)

    async def _abort(self, interaction: discord.Interaction):
        if interaction.user.id != self.ic.id:
            return await interaction.response.send_message("❌ Only the Incident Commander can abort the operation.", ephemeral=True)
        
        await interaction.response.send_modal(AbortReasonModal(self))

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
        """Send the readiness update, preferring Components v2 if available."""
        container, flags = _try_build_v2_readiness(
            self.ic, self.postal, self.assignments, self.states
        )
        if container and flags:
            # Components v2 path — rich layout, no embed
            await interaction.response.edit_message(
                content=None,
                embed=None,
                components=[container],
                view=self,
                flags=flags,
            )
        else:
            # Standard embed fallback
            embed = _embed_readiness(self.ic, self.postal, self.assignments, self.states, self.start_time, self.target_gang, self.image_url)
            await interaction.response.edit_message(embed=embed, view=self)

    # ── Initiate ──────────────────────────────────────────────────────────────

    async def _initiate(self, interaction: discord.Interaction):
        if interaction.user.id != self.ic.id:
            return await interaction.response.send_message(
                "❌ Only the Incident Commander can initiate.", ephemeral=True
            )

        await self.cog.live_ops.update_one(
            {"_id": self.db_id},
            {"$set": {"status": "Active", "initiated_at": datetime.datetime.now(datetime.timezone.utc)}}
        )

        self.status = "Active" # Update internal status
        self._rebuild() # Rebuild the view to remove the initiate button

        embed = _embed_initiated(self.ic, self.postal, self.assignments, self.target_gang, self.image_url)
        await interaction.response.edit_message(
            content="",
            embed=embed,
            view=self, # Keep the view active so abort button remains
        )