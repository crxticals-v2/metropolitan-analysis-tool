"""
cogs/handbook.py

LAPD Metropolitan Standard Operations Handbook
Interactive viewer using Discord Components V2 (raw HTTP).

Drop into your cogs/ folder and add to your setup_hook:
    await bot.load_extension("cogs.handbook")
"""

import discord
from discord import app_commands
from discord.ext import commands
import aiohttp

# ─────────────────────────────────────────────────────────────────────────────
# DISCORD COMPONENTS V2  ·  LOW-LEVEL PAYLOAD HELPERS
# ─────────────────────────────────────────────────────────────────────────────
# Flag that tells Discord this message uses the v2 component system.
# Must be present on every send/edit payload — cannot be mixed with embeds.
_V2_FLAG = 1 << 15

def _text(content: str) -> dict:
    """Type 10 — TextDisplay.  Supports full Discord markdown."""
    return {"type": 10, "content": content}

def _sep(spacing: int = 1, divider: bool = True) -> dict:
    """Type 14 — Separator.  spacing: 1 = small gap, 2 = large gap."""
    return {"type": 14, "divider": divider, "spacing": spacing}

def _thumbnail(url: str) -> dict:
    """Type 11 — Thumbnail accessory (used inside Section)."""
    return {"type": 11, "media": {"url": url}}

def _section(text: str, thumb_url: str = None) -> dict:
    """Type 9 — Section: left-side text + optional right-side thumbnail."""
    s: dict = {"type": 9, "components": [{"type": 10, "content": text}]}
    if thumb_url:
        s["accessory"] = _thumbnail(thumb_url)
    return s

def _container(children: list, color: int = None) -> dict:
    """Type 17 — Container: wraps any v2 components with optional accent stripe."""
    c: dict = {"type": 17, "components": children}
    if color is not None:
        c["accent_color"] = color
    return c

def _btn(label: str, custom_id: str, style: int = 2, disabled: bool = False) -> dict:
    """
    Type 2 — Button.
    style: 1 Primary (blurple), 2 Secondary (grey), 3 Success (green),
           4 Danger (red), 5 Link.
    """
    return {
        "type": 2,
        "label": label,
        "custom_id": custom_id,
        "style": style,
        "disabled": disabled,
    }

def _row(buttons: list) -> dict:
    """Type 1 — ActionRow.  Max 5 buttons per row."""
    return {"type": 1, "components": buttons}


# ─────────────────────────────────────────────────────────────────────────────
# SECTION META
# ─────────────────────────────────────────────────────────────────────────────

_THUMB = "https://i.imgur.com/qdvbBqe.png"   # Metro logo — swap if needed

_NAV: list[tuple[str, str]] = [
    ("intro",       "S-I"),
    ("command",     "S-II"),
    ("personnel",   "S-III"),
    ("deployment",  "S-IV"),
    ("tactical",    "S-V"),
    ("majorcrime",  "S-VI"),
    ("incident",    "S-VII"),
    ("logistics",   "S-VIII"),
    ("cqb",         "S-IX"),
]

_COLORS: dict[str, int] = {
    "intro":       0xB41E1E,   # LAPD Red
    "command":     0xC8A84B,   # Gold
    "personnel":   0x1A3A7A,   # Navy
    "deployment":  0x7B0000,   # Dark Crimson
    "tactical":    0x1B5E20,   # Forest Green
    "majorcrime":  0x4A0072,   # Purple
    "incident":    0x0D47A1,   # Royal Blue
    "logistics":   0x37474F,   # Slate
    "cqb":         0xBF360C,   # Burnt Orange
}


# ─────────────────────────────────────────────────────────────────────────────
# SECTION CONTENT BUILDERS
# ─────────────────────────────────────────────────────────────────────────────

def _s_intro() -> list:
    return [
        _section(
            "## LAPD Metropolitan Division\n### Standard Operations Handbook  ·  `v0.9.1`\n"
            "-# CONFIDENTIAL — DO NOT LEAK — Violations result in immediate termination and blacklist.",
            _THUMB,
        ),
        _sep(spacing=2),
        _text(
            "### 📋  Handbook Purpose\n"
            "The Los Angeles Metropolitan Unit operates as a specialised unit tasked with addressing "
            "**high-risk incidents**, mafia crime suppressions, and rapid response operations across the City of Los Angeles. "
            "This handbook covers the Standard Operating Procedures (SOPs) that allow our Unital personnel to have clear "
            "guidance on our conduct, restrictions, and any administrative expectations set by the **Board of Chiefs (BoC)**."
        ),
        _sep(),
        _text(
            "### 🏙️  What is the Metropolitan Unit?\n"
            "Metropolitan Unit is a specialised unit within LAPD tasked with handling high-risk operations, organised crime, "
            "and violent offenders across Los Angeles. We focus on **proactive enforcement** — targeting armed criminals, "
            "repeat offenders, and coordinated criminal groups through the active use of **pattern recognition**. "
            "The Unit suppresses serious crime through operational deployments, tactical actions, and support to patrol units."
        ),
        _sep(),
        _text(
            "### 🔍  What Does Metro Do?\n"
            "The Metropolitan Unit conducts armed patrol and targets organised crime groups and repeat offenders — "
            "including mafias and gangs — by gathering intelligence and disrupting their operations. Officers may "
            "conduct **undercover work** to obtain critical information (plans, movements, internal structure), "
            "or conduct armed patrol to scout out repeated patterns of high-risk individuals."
        ),
        _sep(),
        _text(
            "### ⚖️  Legal Authority & Jurisdiction\n"
            "As Metropolitan Operatives, you are limited to standard LAPD jurisdiction — **from the City to the East of HW55 SB**.\n\n"
            "If you suspect active mafia activities that may spill over into our jurisdiction from outside LAPD JD, "
            "you may exceed pre-defined LAPD JD **with notification** of local police (LASD, CHP, or applicable local LE).\n\n"
            "> **LA IMPACT members** have permission to exit JD to gather intel or apprehend high-risk suspects. "
            "You must inform the highest-ranking USMS/LASD online of any activities taken outside of LAPD JD."
        ),
        _sep(),
        _text(
            "### 📅  Weekly Quota Requirements\n"
            "| Role | Requirement |\n"
            "|---|---|\n"
            "| Metro Operatives | 1 hour on-duty activity |\n"
            "| Senior Officer+ (incl. MCS) | 1 Mass Training Session (host or co-host; waived if <5 trainees) |\n"
            "| Metro Inspectors | 30 minutes HR Quota |\n"
            "| Promotional | 2 hours · Double Promotion = 7 hours *(B/C Platoon only)* |"
        ),
        _sep(),
        _text(
            "### 📻  Callsign Restrictions\n"
            "All Metro Operatives must use the following callsign formats:\n"
            "- `7M-###` — Metro Standard Callsigns\n"
            "- `K9-###` — Metro K-Platoon Callsigns"
        ),
    ]


def _s_command() -> list:
    return [
        _section("## [S-II]  Command Structure\n-# LAPD Metropolitan Unit", _THUMB),
        _sep(spacing=2),
        _text(
            "### 🏛️  Organisational Overview\n"
            "The Metropolitan Unit operates under a tiered command structure headed by the **Director**. "
            "Below the Director are the Deputy Director and Assistant Director, supported by Executive Overseers "
            "and Chief Inspectors on each command side."
        ),
        _sep(),
        _text(
            "### 👥  Command Hierarchy\n"
            "- 🔴  **Director** — `7L-100` | crxticals\n"
            "- 🟠  **Deputy Director** — Vacant *(×4)*\n"
            "- 🟡  **Chief Inspector — Bronze Command** — Vacant *(×2)*\n"
            "- 🟡  **Chief Inspector — Gold Command** — Vacant *(×2)*"
        ),
        _sep(),
        _text(
            "### 🏅  Unit Breakdown\n\n"
            "**Gold Command — Investigative Response (Major Crimes)**\n"
            "Supervisory Sergeant ×2  ·  Senior Detective ×4  ·  Junior Detective *(unlimited)*\n\n"
            "**Gold Command — Specialist Assignments**\n"
            "Confidential Informant Program ×3  ·  Gang Impact ×4"
        ),
        _sep(),
        _text(
            "**Bronze Command — B/C Platoon (Armed Response)**\n"
            "Supervisory Sergeant ×4  ·  Senior Operative *(unlimited)*  ·  Junior Operative *(unlimited)*  ·  Probationary Operative *(unlimited)*\n\n"
            "**Bronze Command — Specialist Assignments**\n"
            "K-Platoon ×10  ·  Gang Impact ×7  ·  Specialist Protection *(as needed)*"
        ),
        _sep(),
        _text(
            "### 🎖️  Specialisations\n\n"
            "**LA IMPACT** — Targets gang activity alongside the Major Crimes Section and Detective Bureau. "
            "Members with clearance may exit LAPD JD to gather intel or apprehend high-risk suspects. *(Still a proposal)*\n\n"
            "**Specialist Protection** — Responsible for protecting dignitaries and politicians across Los Angeles.\n\n"
            "**K-9 Platoon** — Handlers paired with trained dogs for search, tracking, and tactical deployments."
        ),
    ]


def _s_personnel() -> list:
    return [
        _section("## [S-III]  Personnel Standards & Restrictions\n-# LAPD Metropolitan Unit", _THUMB),
        _sep(spacing=2),
        _text(
            "### 👔  Uniforms & Identification\n"
            "- **Class B/C Uniform** — Standard LAPD patrol uniforms.\n"
            "- **Metropolitan Uniform** — Standard LAPD Metro Long Sleeves uniform.\n"
            "- **Undercover (UC) Clothing** — Civilian attire for blending into high-crime areas or conducting surveillance. "
            "No formal restriction provided it is not a troll avatar.\n"
            "- **Tactical Gear** — Tactical Vest is authorised and **mandatory** over UC or Class B/C during day-to-day operations. "
            "Full tactical gear (helmets, NVGs, etc.) is only authorised for **high-risk operations**."
        ),
        _sep(),
        _text(
            "### 🚗  Vehicle Policy\n"
            "Metro members are strictly assigned to **Unmarked / Undercover (UC) vehicles**. "
            "Marked vehicles are only used during specific raid operations.\n\n"
            "**All Metro Members**\n"
            "> Bullhorn Prancer Pursuit 2015  ·  Falcon Utility Interceptor 2019  ·  Chelvon Camion PPV 2018/2021\n"
            "> Chevlon Commuter Van 2006  ·  Chevlon Platoro PPV 2019  ·  BKM Munich 2020\n"
            "> Bullhorn Foreman 1988  ·  Chevlon Inferno 1981\n\n"
            "**Supervisory+**\n"
            "> Bullhorn Prancer Pursuit Widebody 2020  ·  Averon Q8  ·  Bullhorn BH15 SSV\n\n"
            "**Chief Inspector+**\n"
            "> SWAT Truck 2011  ·  Stuttgart Runner 2020  ·  Emergency Services Falcon Advance+  ·  Mobile Command Centre\n\n"
            "**Senior High Rank+**\n"
            "> Bullhorn Determinator SFP Fury 2022  ·  Falcon eStallion 2024  ·  Bullhorn Prancer Widebody Pursuit"
        ),
        _sep(),
        _text(
            "### 🔧  Vehicle Accessories & Configuration\n"
            "**Lightbar** — Visor Lightbar  ·  Dash Light  ·  Rear Lightstick  ·  Spoiler Lights\n"
            "**Livery** — UC Black or Smokey Grey *(standard)*  ·  Marked *(raid ops only)*\n"
            "**Additional Lighting** — Side Window Light  ·  Fog Light\n"
            "**Accessories** — Pushbar *(opt)*  ·  ANPR *(opt)*  ·  UC Plate *(required)*  ·  "
            "Wheel Covers *(opt)*  ·  Civilian Horn  ·  Cage *(opt — required for prisoner transport)*\n\n"
            "> Metro operatives may go full UC configuration with any of the vehicles listed."
        ),
        _sep(),
        _text(
            "### 🔫  Weapons Authorisation\n\n"
            "**Pistols** — Shield 9  ·  Glock 17  ·  FN Five-Seven  ·  Sauer P226  ·  M&P 9\n\n"
            "**Shotguns** — Fabarm FP6  ·  Berreli M5\n\n"
            "**Assault Rifles / SMGs** — M4A1  ·  MP5  ·  Type 89  ·  M&P 15"
        ),
    ]


def _s_deployment() -> list:
    return [
        _section("## [S-IV]  Deployment Doctrine\n-# LAPD Metropolitan Unit", _THUMB),
        _sep(spacing=2),
        _text(
            "### 📡  Response Criteria\n"
            "Metro operatives are authorised to respond to the following situations:\n\n"
            "- 🆘  **Panic Buttons** — All officer-down or panic button activations.\n"
            "- ⚠️  **High-Risk Calls** — Any situation involving 2–3+ suspects or heavy weaponry.\n"
            "- 🎯  **Barricaded / Active Shooters** — Response as first responders; SWAT may take over mid-scene at their discretion.\n"
            "- 🌟  **High-Value Targets** — Arresting individuals with 3–4+ Stars for transport and interrogation.\n"
            "- 🏴  **Gang / Mafia Activity** — Any scene involving known criminal organisations or escalating tensions between crime families."
        ),
        _sep(),
        _text(
            "### 🗺️  Patrol & Deployment\n"
            "Unlike standard patrol units, Metro members are granted significantly greater operational freedom:\n\n"
            "- **Proactive Hunting** — You are not required to wait at the PD for calls. Actively patrol 'hot spots' with "
            "high fugitive/gang activity — housing suburbs, the Jewelry Store, or any known criminal drop spots.\n\n"
            "- **Self-Dispatch** — You have the authority to self-attach to calls that meet Metro criteria without waiting for dispatch."
        ),
        _sep(),
        _text(
            "### 🎙️  Field Conduct\n\n"
            "**Interrogation** — When a high-level suspect is apprehended, prioritise securing them for questioning. "
            "Metro focuses on **long-term intelligence-gathering** over arrest logging.\n\n"
            "**Communication** — Always announce when you are 'jumping' a call so standard patrol knows Metro is taking point."
        ),
        _sep(),
        _text(
            "### 📜  Rules of Engagement\n"
            "- Identify yourself as law enforcement before engaging unless operational security demands otherwise.\n"
            "- Exhaust **de-escalation** options before advancing to lethal force.\n"
            "- Do **not** engage civilian bystanders under any circumstance.\n"
            "- Out-of-jurisdiction pursuits require prior notification to local law enforcement — per current LA:RP regulations."
        ),
    ]


def _s_tactical() -> list:
    return [
        _section("## [S-V]  Tactical Operations\n-# LAPD Metropolitan Unit", _THUMB),
        _sep(spacing=2),
        _text(
            "### 🏚️  High-Risk Warrant Service\n"
            "High-risk warrant operations are coordinated through **Gold Command — Major Crimes Section** and require:\n"
            "- Pre-operational briefings\n"
            "- Designated entry teams\n"
            "- Communication with SWAT when escalated force is anticipated"
        ),
        _sep(),
        _text(
            "### 🐕  K-9 Operations  —  Core Directives\n"
            "- **Direct Control** — The K-9 must remain within **10 studs** of the Handler at all times unless a specific command is issued.\n"
            "- **Verbal Warning** — The Handler is required to issue a verbal warning **before** releasing the dog on any suspect.\n"
            "- **No Solo Patrols** — A K-9 Unit is one Handler and one dog — a single tactical cell that does not split up.\n\n"
            "**Callsigns:** `7K-9XX`  (K-901 through K-914) — Maximum 14 K-9 personnel; higher ranks receive priority assignments."
        ),
        _sep(),
        _text(
            "**K-9 Dog Abilities**\n"
            "- **Vehicle Search (Sniff)** — Searches the boot; barks on detection of weapons or gun residue (primer, explosive, smokeless powder).\n"
            "- **Person Search (Sniff)** — Equip a taser to view the weapon indicator; dog barks if a weapon is detected.\n"
            "- **Attack** — Use a beanbag shotgun or pepper spray against suspects only to simulate a bite engagement.\n\n"
            "**K-9 Search Legal Standards**\n"
            "K-9 searches are investigative techniques requiring **reasonable suspicion**, not probable cause. "
            "Articulable facts — inconsistent statements, nervousness, gang-affiliated plates, or credible intelligence — "
            "are sufficient to deploy K-9. The dog cannot indicate on items other than weapons.\n\n"
            "**K-9 Tracking Operations**\n"
            "Units may sparingly follow gang vehicles seen running gun deals. Attempt a natural stop on a traffic infraction, "
            "then execute a standard K-9 search. If occupants are planning a mass operation, radio SWAT for additional support "
            "and use K-9 as an intimidation tactic."
        ),
        _sep(),
        _text(
            "### 🛡️  Principal Protection Procedures\n"
            "Deployment details are issued per-operation by Gold/High Command. Standard close-protection terminology:\n\n"
            "| Term | Definition |\n"
            "|---|---|\n"
            "| **Principal** | Individual being protected |\n"
            "| **Motorcade** | The convoy of vehicles |\n"
            "| **PPO** | Principal Protection Officer — lead officer |\n"
            "| **PO** | Protection Officer — supporting personnel |\n"
            "| **Lead Car** | Clears and scouts the route ahead |\n"
            "| **Trail Car** | Positioned behind the principal |\n"
            "| **Flankers** | Agents positioned to the sides (on foot) |\n"
            "| **Bubble** | Immediate protective space around the principal |\n"
            "| **Shadow** | Discreet nearby positioning without drawing attention |\n"
            "| **Diamond/Box Formation** | Standard close-protection formation |\n"
            "| **Sitrep** | Shorthand for situation report |"
        ),
        _sep(),
        _text(
            "**Protection Detail Requirements**\n"
            "Minimum **3 UC vehicles**: one carrying the Principal and PPO, one carrying backup Metro Operatives, and one as a decoy. "
            "All vehicles must be unlocked to all protection detail members.\n\n"
            "> **Do NOT** refer to the Principal by their actual name during any communications. Use code-words such as "
            "*Lavender*, *Tulip*, *Goldenhand*, or any professionally appropriate substitute."
        ),
        _sep(),
        _text(
            "### 🚧  Crowd Control & Civil Unrest\n"
            "Metro units may be deployed to support patrol in high-tension crowd situations.\n\n"
            "- Maintain designated formation\n"
            "- Utilise verbal commands before any physical engagement\n"
            "- Coordinate through incident command\n"
            "- Set up crowd fences and riot shields to establish a basic cordon over protected areas\n"
            "- If protesters are excessively rowdy — hold your grounds\n"
            "- If protesters **break the cordon** — use non-lethal methods only *(Pepper Spray or Baton)* for unarmed suspects"
        ),
    ]


def _s_majorcrime() -> list:
    return [
        _section("## [S-VI]  Major Crimes Section\n-# LAPD Metropolitan Unit", _THUMB),
        _sep(spacing=2),
        _text(
            "The **Major Crimes Section** operates under Gold Command and handles investigative response operations, "
            "coordinated mafia suppression, and Counter-Terrorism Command activities.\n\n"
            "Specific operational details, active cases, and intelligence files are maintained in **restricted channels** "
            "accessible to authorised Gold Command personnel+ only."
        ),
        _sep(),
        _text(
            "### 📝  Joining Major Crimes\n"
            "Any **Junior Metro Operative+** may apply to switch to the investigative branch. "
            "They will still be permitted to patrol as B/C Platoon, but will now:\n\n"
            "- Hold significantly more investigative powers\n"
            "- More likely to manage UC infiltration operations\n"
            "- More likely to build experience coordinating multiple UC agents across a plethora of avenues\n\n"
            "Entry requires a **simple exam** demonstrating investigative abilities — Metro entry training has already "
            "certified operatives to the proficient level of investigations.\n\n"
            "> All Detective Bureau members may also take a short secondment *(~1–2 weeks)* at Metro Major Crimes Section "
            "to be evaluated for direct entry."
        ),
        _sep(),
        _text(
            "### 🔬  What Major Crimes Does\n"
            "One of their primary works is **analysing criminal activity patterns** of high-risk suspects online. "
            "For example, suspects like *Marrabelle* have defined patterns in their robberies:\n\n"
            "> She robs jewelry, tool stores, housing suburbs, and the county gas station — "
            "then when police catch on, she circles back, heads to the end of Freedom Avenue, "
            "uses the shortcut to county jail, circles the farms, and goes down Riverside.\n\n"
            "Building these profiles enables **direct arrests** through effective predictive analysis on future suspect "
            "behaviours — anticipating possible events **before** they even begin their robbery spree."
        ),
    ]


def _s_incident() -> list:
    return [
        _section("## [S-VII]  Incident Command\n-# LAPD Metropolitan Unit", _THUMB),
        _sep(spacing=2),
        _text(
            "### 🎖️  Incident Command System (ICS)\n"
            "All major incidents will be managed under an **ICS framework**. The highest-ranking Metro operative on scene "
            "assumes Incident Command until relieved — unless an officer of lower rank holds the **Incident Command Certificate**.\n\n"
            "The IC is responsible for:\n"
            "- Scene organisation\n"
            "- Unit coordination\n"
            "- Communications with dispatch"
        ),
        _sep(),
        _text(
            "### 🤝  Multi-Agency Coordination\n"
            "When operating alongside **LASD, CHP, USMS**, or other agencies — particularly in out-of-jurisdiction scenarios — "
            "the ranking Metro operative must:\n"
            "1. Brief the highest-ranking officer from the cooperating agency\n"
            "2. Establish shared communication channels"
        ),
        _sep(),
        _text(
            "### 🏢  Command Post Operations\n"
            "Establish a command post at a **safe distance** from the active scene. "
            "The **Mobile Command Centre** *(Chief Inspector+ only)* may be deployed for extended operations requiring on-site coordination hubs."
        ),
        _sep(),
        _text(
            "### ⚙️  Tactical Decision-Making Process\n\n"
            "**1.** Gather intelligence and establish a scene perimeter.\n"
            "**2.** Assign roles — entry, cover, negotiation, command, sniper, etc.\n"
            "**3.** Brief all units on the mission profile before action.\n"
            "**4.** Execute, monitor, and adapt based on developing intelligence.\n"
            "**5.** Debrief following operation completion and submit a field report."
        ),
    ]


def _s_logistics() -> list:
    return [
        _section("## [S-VIII]  Internal Logistics\n-# LAPD Metropolitan Unit", _THUMB),
        _sep(spacing=2),
        _text(
            "### 💎  Intel Point System (IP System)\n"
            "Officers earn **Intel Points (IP)** based on the quality and value of intelligence provided to the Unit. "
            "The High Ranking / Senior High Ranking team awards **1–3 points per submission** depending on operational impact. "
            "IP can be exchanged in the **Metro Intel Rewards Shop**."
        ),
        _sep(),
        _text(
            "**Intel Point Earnings**\n"
            "| Activity | Points Awarded |\n"
            "|---|---|\n"
            "| After Action Reports (Raids, stakeouts, etc.) | 2–4 Points |\n"
            "| Field Reports | 1–2 Points |\n"
            "| Mafia Sightings | 1–3 Points |\n"
            "| 5× Logs on 1× Repeat Offender *(server-frequent suspects)* | 1–2 Points |"
        ),
        _sep(),
        _text(
            "**Intel Rewards Shop**\n"
            "| Reward | Cost |\n"
            "|---|---|\n"
            "| Shift Time Extension (+15 minutes) | 3 Points |\n"
            "| Shift Time Extension (+30 minutes) | 6 Points |\n"
            "| Hint on next Drill/Hunt | 5 Points |\n"
            "| Quota Exemption (1 Week) | 12 Points |"
        ),
        _sep(),
        _text(
            "### 📋  Duty Logging\n"
            "All Metro officers are required to log any significant findings and activity when on duty in their designated logging channel.\n\n"
            "If you notice any **repeat offender's pattern**, logging it is extremely beneficial — it directly feeds the unit's "
            "predictive analysis pipeline on suspects.\n\n"
            "> **K-9 handlers** must additionally log every K-9 deployment after deploying dogs during their shift."
        ),
    ]


def _s_cqb() -> list:
    return [
        _section("## [S-IX]  Basic Close Quarter Combat Tactics\n-# LAPD Metropolitan Unit", _THUMB),
        _sep(spacing=2),
        _text(
            "Close-Quarters Battle **(CQB)** is high-intensity and rapid — it requires quick decisions and decisive actions "
            "taken within seconds. Confined spaces limit visibility and maneuverability, meaning traditional field tactics do not apply. "
            "CQB is typically conducted in **teams of 1–3 operators**.\n\n"
            "> Note: On Metro duties you will be very unlikely to use CQB day-to-day, but it is best practice to know these."
        ),
        _sep(),
        _text(
            "### ⚡  Core Characteristics\n"
            "- **Teamwork** — Non-negotiable. If operators do not know what teammates are doing, the entire operation can collapse instantly.\n"
            "- **Speed & Surprise** — Move quickly and use the element of surprise against suspects. Always brief your team before executing.\n"
            "- **Aim Control** — Control your aim at all times — confined spaces may contain hostages or civilians behind or near suspects."
        ),
        _sep(),
        _text(
            "### 🚪  Steps to Clear a Room\n\n"
            "**1.** Clear as much of the room as possible from the **exterior** before entry — significantly reduces risk by "
            "neutralising threats before stepping into space.\n\n"
            "**2.** Perform a quick assessment: identify the appropriate breach technique (J-Hook, Cross Breach, or combined). "
            "Determine whether to go in **loud or quiet**.\n\n"
            "**3.** Execute the breach: move quickly but with control. *Slow is steady, steady is fast.* "
            "Secure all threats by arrest or takedown as the situation demands."
        ),
        _sep(),
        _text(
            "### 💥  Breach Techniques\n\n"
            "**J-Hook Breach**\n"
            "Operators 'hook' around the door/wall frame in a J-shaped movement, hugging the wall while clearing visible angles first. "
            "Used with a **single stack formation** — best when a door opens to a wall directly ahead. "
            "Keeps operators out of each other's line of fire and keeps the pointman out of the **fatal funnel** longest.\n\n"
            "**Slicing the Pie**\n"
            "The operator gradually exposes small portions of an area by pivoting at an angle rather than stepping directly into the unknown. "
            "Used when approaching corners, blind spots, or entryways. Typically performed by **2 operators** — one slicing, one providing cover — "
            "then transitioning into a cross breach.\n\n"
            "**Cross Breach**\n"
            "Lead operators stack up on either side of the door frame, then **cross simultaneously** into the room. "
            "Gets operators in faster and immediately provides coverage of opposite sectors. "
            "Highly efficient when rapid room clearance is required — uses a **double stack formation**."
        ),
        _sep(),
        _text(
            "### 🏢  Hallway Procedures\n\n"
            "**L-Shaped Hallway** — Operator 1 calls 'Hallway left/right'. Operator 2 moves up to the second wall "
            "for a faster, clearer visual of the next room and is better positioned to secure threats.\n\n"
            "**T-Shaped Intersection** — Both pointmen position on either side of the T. They raise barrels to signal J-hook, "
            "then lower simultaneously to execute. After breach, both regroup at the T-intersection.\n\n"
            "**Stairwell** — Operator 1 looks up to scan for threats above. Operator 2 and additional operators cover the front. "
            "Rear man holds the staircase to prevent flanking. When ready to move, tap the rear man's shoulder — rear man takes point."
        ),
        _sep(),
        _text(
            "### 💡  CQB Tips\n"
            "- **Situational Awareness** — Always scan: exits, cover positions, teammate locations, and obstacles.\n"
            "- **Communication** — Keep callouts clear, concise, and quick. Short callouts keep the team informed without slowing the operation.\n"
            "- **Avoid the Fatal Funnel** — The doorway is maximally exposing. Do not linger — get through fast and move to cover.\n"
            "- **Use Angles** — Slicing the Pie reveals threats one angle at a time without full exposure.\n"
            "- **Team Coordination** — Highest-ranking operator assigns roles before entry. Every operator must know their position.\n"
            "- **Speed & Control** — Move with controlled speed to overwhelm threats. Do not rush blindly — maintain discipline.\n"
            "- **Target Identification** — Quickly identify what suspects are carrying and relay that information to team and units on scene.\n"
            "- **Adaptability** — Scenes change without warning. Adapt your plan quickly with your team as new information develops."
        ),
    ]


# ─────────────────────────────────────────────────────────────────────────────
# SECTION REGISTRY
# ─────────────────────────────────────────────────────────────────────────────

_BUILDERS = {
    "intro":       _s_intro,
    "command":     _s_command,
    "personnel":   _s_personnel,
    "deployment":  _s_deployment,
    "tactical":    _s_tactical,
    "majorcrime":  _s_majorcrime,
    "incident":    _s_incident,
    "logistics":   _s_logistics,
    "cqb":         _s_cqb,
}


# ─────────────────────────────────────────────────────────────────────────────
# MESSAGE ASSEMBLER
# ─────────────────────────────────────────────────────────────────────────────

def build_message(active: str) -> list:
    """
    Assemble the full Components V2 payload for the handbook viewer.

    Returns a list of top-level component dicts ready to be sent via
    the Discord REST API with IS_COMPONENTS_V2 flag (1 << 15).
    """
    # ── Main content container ───────────────────────────────────────────────
    content_children = _BUILDERS[active]()
    main = _container(content_children, color=_COLORS[active])

    # ── Navigation — Row 1: S-I → S-V  (5 buttons max per row) ──────────────
    nav1 = _row([
        _btn(label, f"hb_{key}", style=1 if key == active else 2)
        for key, label in _NAV[:5]
    ])

    # ── Navigation — Row 2: S-VI → S-IX  (4 buttons) ────────────────────────
    nav2 = _row([
        _btn(label, f"hb_{key}", style=1 if key == active else 2)
        for key, label in _NAV[5:]
    ])

    return [main, nav1, nav2]


# ─────────────────────────────────────────────────────────────────────────────
# RAW HTTP HELPERS
# These bypass discord.py's response tracking so we can set IS_COMPONENTS_V2.
# ─────────────────────────────────────────────────────────────────────────────

async def _initial_respond(interaction: discord.Interaction, components: list) -> None:
    """
    Type-4 interaction callback (CHANNEL_MESSAGE_WITH_SOURCE) with V2 flag.
    Called for the initial /metro_handbook slash command.
    """
    url = (
        f"https://discord.com/api/v10/interactions/"
        f"{interaction.id}/{interaction.token}/callback"
    )
    payload = {
        "type": 4,
        "data": {
            "flags": _V2_FLAG | 64,
            "components": components,
        },
    }
    async with aiohttp.ClientSession() as session:
        resp = await session.post(url, json=payload)
        if resp.status not in (200, 204):
            text = await resp.text()
            raise RuntimeError(f"Discord API error {resp.status}: {text}")


async def _update_respond(interaction: discord.Interaction, components: list) -> None:
    """
    Type-7 interaction callback (UPDATE_MESSAGE) with V2 flag.
    Called when a navigation button is clicked.
    """
    url = (
        f"https://discord.com/api/v10/interactions/"
        f"{interaction.id}/{interaction.token}/callback"
    )
    payload = {
        "type": 7,
        "data": {
            "flags": _V2_FLAG | 64,
            "components": components,
        },
    }
    async with aiohttp.ClientSession() as session:
        resp = await session.post(url, json=payload)
        if resp.status not in (200, 204):
            text = await resp.text()
            raise RuntimeError(f"Discord API error {resp.status}: {text}")


# ─────────────────────────────────────────────────────────────────────────────
# COG
# ─────────────────────────────────────────────────────────────────────────────

class HandbookCog(commands.Cog, name="Handbook"):
    """
    Serves the LAPD Metropolitan Standard Operations Handbook
    as an interactive Components V2 message.
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── Slash command ─────────────────────────────────────────────────────────

    @app_commands.command(
        name="metro_handbook",
        description="Open the LAPD Metropolitan Standard Operations Handbook.",
    )
    async def metro_handbook(self, interaction: discord.Interaction) -> None:
        components = build_message("intro")
        await _initial_respond(interaction, components)

    # ── Button interaction handler ────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        """
        Intercept component interactions whose custom_id starts with 'hb_'
        and re-render the handbook for the requested section.

        This listener fires for ALL interactions — guard clauses keep it fast.
        """
        if interaction.type is not discord.InteractionType.component:
            return

        custom_id: str = (interaction.data or {}).get("custom_id", "")
        if not custom_id.startswith("hb_"):
            return

        section_key = custom_id[3:]   # strip leading "hb_"
        if section_key not in _BUILDERS:
            return

        components = build_message(section_key)
        await _update_respond(interaction, components)


# ─────────────────────────────────────────────────────────────────────────────
# SETUP
# ─────────────────────────────────────────────────────────────────────────────

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HandbookCog(bot))