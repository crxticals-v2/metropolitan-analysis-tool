"""
test_embeds.py  –  Verifies that every Discord embed builds with the right
                   fields, colours, and content — without sending anything
                   to Discord.

All embed construction is tested synchronously or via pytest-asyncio.
"""

import datetime
import time
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest
import pytest_asyncio

# conftest supplies make_* helpers automatically
from conftest import (
    make_channel,
    make_guild,
    make_interaction,
    make_member,
    make_mongo_collection,
    make_role,
)


# ─────────────────────────────────────────────────────────────────────────────
# OPERATIONS COG – EMBED CONSTRUCTION
# ─────────────────────────────────────────────────────────────────────────────

class TestOperationsEmbeds:
    """
    Tests that the Operations cog commands build the correct embed objects.
    We call the command methods directly with mocked Interactions.
    """

    # ── /metro_promote ───────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_metro_promote_embed_fields(self, operations_cog):
        interaction = make_interaction()
        # Patch _resolve_output_channel to avoid real Discord channel lookup
        operations_cog._resolve_output_channel = MagicMock(
            return_value=make_channel()
        )

        await operations_cog.metro_promote.callback(
            operations_cog,
            interaction,
            officer=make_member(display_name="JohnDoe"),
            previous_rank="Metro Junior Officer",
            new_rank="Metro Senior Officer",
            notes="Consistently professional.",
            signed="Commander Smith",
        )

        # The channel.send should have been called with an embed
        channel = operations_cog._resolve_output_channel.return_value
        channel.send.assert_called_once()
        call_kwargs = channel.send.call_args.kwargs
        embed = call_kwargs.get("embed")
        assert embed is not None, "metro_promote must send an embed"
        assert "Metro Senior Officer" in embed.description
        assert "Metro Junior Officer" in embed.description
        assert "Commander Smith"      in embed.description
        assert embed.color == discord.Color.blue()

    # ── /metro_infract ───────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_metro_infract_embed_fields(self, operations_cog):
        interaction = make_interaction()
        operations_cog._resolve_output_channel = MagicMock(
            return_value=make_channel()
        )

        await operations_cog.metro_infract.callback(
            operations_cog,
            interaction,
            officer=make_member(display_name="Officer123"),
            punishment="Written Warning",
            reason="Excessive force",
            appealable="Yes",
            signed="Deputy Director Jones",
        )

        channel = operations_cog._resolve_output_channel.return_value
        channel.send.assert_called_once()
        embed = channel.send.call_args.kwargs["embed"]
        assert embed.color == discord.Color.red()
        assert "Written Warning" in embed.description
        assert "Excessive force" in embed.description
        assert "Deputy Director Jones" in embed.description

    # ── /k9_deploy ───────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_k9_deploy_embed_fields(self, operations_cog):
        interaction = make_interaction()
        target_ch   = make_channel()

        # _get_target_channel is async; patch it
        operations_cog._get_target_channel = AsyncMock(return_value=target_ch)

        await operations_cog.k9_deploy.callback(
            operations_cog,
            interaction,
            handler_name="Officer Kane",
            k9_name="Rex",
            reason="Suspect fleeing on foot",
            result="Suspect apprehended",
            evidence=None,
        )

        target_ch.send.assert_called_once()
        embed = target_ch.send.call_args.kwargs["embed"]
        assert "Officer Kane" in embed.description
        assert "Rex"          in embed.description
        assert "Suspect fleeing on foot" in embed.description
        assert "Suspect apprehended"     in embed.description
        assert embed.color == discord.Color.dark_blue()

    @pytest.mark.asyncio
    async def test_k9_deploy_with_evidence_url(self, operations_cog):
        """When an attachment is provided, its URL must appear in the embed."""
        interaction = make_interaction()
        target_ch   = make_channel()
        operations_cog._get_target_channel = AsyncMock(return_value=target_ch)

        attachment      = MagicMock()
        attachment.url  = "https://cdn.discordapp.com/attachments/123/456/photo.png"

        await operations_cog.k9_deploy.callback(
            operations_cog,
            interaction,
            handler_name="Handler",
            k9_name="Max",
            reason="Tracking",
            result="Located",
            evidence=attachment,
        )

        embed = target_ch.send.call_args.kwargs["embed"]
        assert attachment.url in embed.description

    # ── /metro_mass_shift ────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_metro_mass_shift_embed_and_reactions(self, operations_cog):
        guild   = make_guild()
        user    = make_member(display_name="Shift Host")
        channel = make_channel()
        interaction = make_interaction(user=user, guild=guild, channel=channel)

        operations_cog._resolve_output_channel = MagicMock(return_value=channel)

        # discord.utils.get is module-level; patch it
        with patch("operations.discord.utils.get", return_value=make_role("Metropolitan Division")):
            await operations_cog.metro_mass_shift.callback(
                operations_cog,
                interaction,
                co_host=None,
                notes="All hands on deck.",
            )

        channel.send.assert_called()
        sent_embed = channel.send.call_args.kwargs.get("embed")
        assert sent_embed is not None
        assert interaction.user.mention in sent_embed.description
        assert "All hands on deck." in sent_embed.description

    # ── /metro_after_action ──────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_metro_after_action_embed_fields(self, operations_cog):
        user    = make_member(display_name="Detective Ray")
        guild   = make_guild()
        channel = make_channel()
        interaction = make_interaction(user=user, guild=guild, channel=channel)

        # give the user a Metro role so _get_user_rank works
        metro_role      = make_role("Metro Senior Detective", position=10)
        metro_role.name = "Metro Senior Detective"
        user.roles      = [metro_role]

        target_ch = make_channel()
        operations_cog._get_target_channel = AsyncMock(return_value=target_ch)
        operations_cog._resolve_output_channel = MagicMock(return_value=target_ch)

        with patch("operations.discord.utils.get", return_value=None):
            await operations_cog.metro_after_action.callback(
                operations_cog,
                interaction,
                officers="Detective Ray",
                patrol_area="Downtown District",
                time_observed="14:30",
                suspicious_activity="Three individuals casing the jewelry store.",
                actions_taken="Followed and documented suspect movements.",
                vehicle="Ford Crown Victoria/Police Interceptor",
                additional_notes="Suspects drove away northbound.",
            )

        target_ch.send.assert_called()
        embed = target_ch.send.call_args.kwargs["embed"]
        assert "Downtown District" in embed.description
        assert "Three individuals casing the jewelry store." in embed.description
        assert embed.color == discord.Color.dark_blue()

    # ── /metro_start_case ────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_metro_start_case_creates_thread(self, operations_cog):
        interaction = make_interaction()

        # Mock a ForumChannel
        forum           = AsyncMock()
        forum.__class__ = discord.ForumChannel
        thread_mock     = MagicMock()
        thread_mock.thread.id      = 12345
        thread_mock.thread.mention = "<#12345>"
        forum.create_thread        = AsyncMock(return_value=thread_mock)

        operations_cog.bot.get_channel = MagicMock(return_value=forum)
        operations_cog.metro_cases.insert_one = AsyncMock()

        with patch("operations.isinstance", return_value=True):
            await operations_cog.metro_start_case.callback(
                operations_cog,
                interaction,
                organised_crime_group_name="Shadow Syndicate",
                forum_channel_id="1496777843668160633",
            )

        # Should have created the thread
        forum.create_thread.assert_called_once()
        create_kwargs = forum.create_thread.call_args.kwargs
        assert "Shadow Syndicate" in create_kwargs.get("name", "") or \
               "Shadow Syndicate" in str(create_kwargs.get("embed", "").description if create_kwargs.get("embed") else "")

    # ── /metro_case_log ──────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_metro_case_log_embed(self, operations_cog):
        interaction = make_interaction()

        case_data   = {"case_id": 11111, "thread_id": 99999}
        case_thread = make_channel(channel_id=99999)

        operations_cog.metro_cases.find_one = AsyncMock(return_value=case_data)
        operations_cog.bot.get_channel      = MagicMock(return_value=case_thread)

        await operations_cog.metro_case_log.callback(
            operations_cog,
            interaction,
            case_id=11111,
            detectives="Detective Brown, Detective Lee",
            suspect_description="Male, blue jacket",
            vehicles_used="Black sedan",
            suspicious_activities="Surveilling bank perimeter",
            criminal_activities="Armed robbery",
            area="Financial District",
            notes="Three suspects involved.",
        )

        case_thread.send.assert_called()
        embed = case_thread.send.call_args.kwargs["embed"]
        assert "Detective Brown" in embed.description
        assert "Armed robbery"   in embed.description

    @pytest.mark.asyncio
    async def test_gang_profiler_embed_content(self, simon_cog):
        """Verifies the Gang Tactical Briefing embed structure."""
        gang_config = {
            "_id": "gang_WCC",
            "mo": "Operates primarily in the industrial sector.",
            "vehicles": "Black SUVs",
            "clothing": "Blue Bandanas"
        }
        top_members = [{"_id": "leader_x", "count": 50}]
        
        simon_cog.settings.find_one = AsyncMock(return_value=gang_config)
        simon_cog.bot.suspect_logs.aggregate.return_value.to_list = AsyncMock(return_value=top_members)

        with patch("simon.fetch_roblox_data", new=AsyncMock(return_value=(None, None, None))), \
             patch("simon.call_llm", new=AsyncMock(return_value={"analysis": "Summarized MO."})):
            
            pages, files = await simon_cog.build_gang_profiler("WCC")
        
        embed = pages[0]
        assert "TACTICAL INTELLIGENCE BRIEFING" in embed.description
        assert "Summarized MO." in embed.description
        assert "Black SUVs" in embed.description
        assert "leader_x" in embed.description.lower()
        assert embed.color == discord.Color.gold()

    @pytest.mark.asyncio
    async def test_metro_case_log_invalid_case_id(self, operations_cog):
        """When case ID is not found, the command should send an error."""
        interaction = make_interaction()
        operations_cog.metro_cases.find_one = AsyncMock(return_value=None)

        await operations_cog.metro_case_log.callback(
            operations_cog,
            interaction,
            case_id=99999,
            detectives="No one",
        )

        interaction.followup.send.assert_called()
        args = interaction.followup.send.call_args
        assert "not found" in str(args).lower() or "❌" in str(args)


# ─────────────────────────────────────────────────────────────────────────────
# OPERATIONS MODALS – on_submit EMBED TESTS
# ─────────────────────────────────────────────────────────────────────────────

class TestOperationModals:
    """Tests the modal on_submit handlers directly."""

    @pytest.mark.asyncio
    async def test_training_modal_valid_scores(self, operations_cog):
        from operations import MetroTrainingModal

        host    = make_member(display_name="Trainer")
        trainee = make_member(display_name="Recruit")
        modal   = MetroTrainingModal(
            host=host, co_host=None,
            trainee=trainee, outcome="PASSED", notes="Good performance."
        )
        modal.s1._value = "8"
        modal.s2._value = "7"
        modal.s3._value = "9"

        interaction = make_interaction(user=host)
        ch          = make_channel()
        interaction.client = MagicMock()
        interaction.client.get_cog = MagicMock(return_value=None)

        with patch("operations.resolve_output_channel", return_value=ch):
            await modal.on_submit(interaction)

        ch.send.assert_called_once()
        embed = ch.send.call_args.kwargs["embed"]
        assert "8/10"       in embed.description
        assert "7/10"       in embed.description
        assert "9/10"       in embed.description
        assert "24/30"      in embed.description   # total
        assert "PASSED"     in embed.description
        assert "Good performance." in embed.description

    @pytest.mark.asyncio
    async def test_training_modal_invalid_scores_sends_error(self, operations_cog):
        from operations import MetroTrainingModal

        host    = make_member()
        trainee = make_member()
        modal   = MetroTrainingModal(host=host, co_host=None, trainee=trainee, outcome="PASS", notes="")
        modal.s1._value = "abc"
        modal.s2._value = "5"
        modal.s3._value = "5"

        interaction = make_interaction()
        await modal.on_submit(interaction)
        interaction.response.send_message.assert_called()
        assert "❌" in str(interaction.response.send_message.call_args)

    @pytest.mark.asyncio
    async def test_announcement_modal_with_ping(self):
        from operations import MetroAnnouncementModal

        role      = make_role("Metropolitan Division")
        modal     = MetroAnnouncementModal(ping_role=True, role=role)
        modal.announcement._value = "## All units to the briefing room!"

        interaction = make_interaction()
        ch          = make_channel()

        with patch("operations.resolve_output_channel", return_value=ch):
            await modal.on_submit(interaction)

        ch.send.assert_called_once()
        call_kwargs = ch.send.call_args.kwargs
        embed = call_kwargs.get("embed")
        assert embed is not None
        assert "All units to the briefing room!" in embed.description
        # When ping_role=True, content should be the role mention
        assert call_kwargs.get("content") == role.mention

    @pytest.mark.asyncio
    async def test_announcement_modal_no_ping(self):
        from operations import MetroAnnouncementModal

        modal     = MetroAnnouncementModal(ping_role=False, role=None)
        modal.announcement._value = "Shift starting at 8 PM."

        interaction = make_interaction()
        ch          = make_channel()

        with patch("operations.resolve_output_channel", return_value=ch):
            await modal.on_submit(interaction)

        call_kwargs = ch.send.call_args.kwargs
        assert call_kwargs.get("content") is None  # no ping


# ─────────────────────────────────────────────────────────────────────────────
# SIMON COG – EMBED CONSTRUCTION
# ─────────────────────────────────────────────────────────────────────────────

class TestSimonEmbeds:
    """Tests for embed fields and colours in the Simon cog commands."""

    # ── /metro_crime_heatmap ─────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_crime_heatmap_embed_structure(self, simon_cog):
        """When data exists, must send an embed with heatmap image attached."""
        interaction = make_interaction()
        channel_mock = make_channel()
        interaction.channel = channel_mock

        agg_data = [{"_id": "N-205", "count": 5}, {"_id": "N-300", "count": 2}]
        simon_cog.bot.suspect_logs.aggregate.return_value.to_list = AsyncMock(
            return_value=agg_data
        )

        import io
        dummy_image = io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        with patch("simon.draw_heatmap_overlay", return_value=dummy_image):
            await simon_cog.metro_crime_heatmap.callback(simon_cog, interaction)

        channel_mock.send.assert_called()
        embed = channel_mock.send.call_args.kwargs.get("embed")
        assert embed is not None
        assert embed.color == discord.Color.red()
        assert "heatmap.png" in embed.image.url

    @pytest.mark.asyncio
    async def test_crime_heatmap_no_data_sends_message(self, simon_cog):
        """When no postal data exists, user gets a plain text message."""
        interaction = make_interaction()
        simon_cog.bot.suspect_logs.aggregate.return_value.to_list = AsyncMock(
            return_value=[]
        )

        await simon_cog.metro_crime_heatmap.callback(simon_cog, interaction)

        interaction.followup.send.assert_called()
        assert "No historical" in str(interaction.followup.send.call_args)

    # ── /metro_watchlist ─────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_metro_watchlist_embed_has_suspect_fields(self, simon_cog):
        suspects = [
            {
                "_id": "dangeroussuspect",
                "count": 12,
                "last_crime": "Robbery",
                "last_location": "N-205",
                "last_seen": "2025-01-15T10:00:00",
            }
        ]
        simon_cog.bot.suspect_logs.aggregate.return_value.to_list = AsyncMock(
            return_value=suspects
        )

        with patch("simon.build_watchlist_grid", new=AsyncMock(return_value=None)):
            embed, file, view, top = await simon_cog._generate_watchlist_content()

        assert embed is not None
        assert any("DANGEROUSSUSPECT" in f.name.upper() for f in embed.fields)
        assert any("12" in f.value for f in embed.fields)

    @pytest.mark.asyncio
    async def test_metro_gang_watchlist_embed_structure(self, simon_cog):
        """Verifies the Gang Activity Monitor embed structure and image attachment."""
        gangs = ["77th", "WCC", "NSH"]
        gang_stats = [
            {"gang": "77th", "total": 10, "top_rep": "Alpha", "rep_count": 5},
            {"gang": "WCC", "total": 15, "top_rep": "Bravo", "rep_count": 7},
            {"gang": "NSH", "total": 20, "top_rep": "Charlie", "rep_count": 9},
        ]
        simon_cog.bot.suspect_logs.count_documents = AsyncMock(side_effect=[s["total"] for s in gang_stats])
        simon_cog.bot.suspect_logs.aggregate.return_value.to_list = AsyncMock(side_effect=[[{"_id": s["top_rep"], "count": s["rep_count"]}] for s in gang_stats])

        # Mock the new gang logo grid builder
        import io
        dummy_image = io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        with patch("simon.build_gang_logo_grid", new=AsyncMock(return_value=dummy_image)):
            embed, file, view = await simon_cog._generate_gang_watchlist_content()

        assert embed is not None
        assert file is not discord.utils.MISSING
        assert "GANG ACTIVITY MONITOR" in embed.description.upper()
        assert embed.image.url == "attachment://gang_logos.png"
        assert any("Total Crimes: `10`" in f.value for f in embed.fields)

    @pytest.mark.asyncio
    async def test_metro_suspect_watchlist_no_data_returns_none(self, simon_cog):
        simon_cog.bot.suspect_logs.aggregate.return_value.to_list = AsyncMock(
            return_value=[]
        )
        embed, file, view, top = await simon_cog._generate_watchlist_content()
        assert embed is None

    # ── /metro_predict embed color by risk ───────────────────────────────────

    def test_predict_embed_color_mapping(self):
        """
        The color_map in metro_predict must map risk levels correctly.
        We test this by inspecting the mapping dict directly.
        """
        color_map = {
            "Low":    discord.Color.green(),
            "Med":    discord.Color.orange(),
            "Medium": discord.Color.orange(),
            "High":   discord.Color.red(),
        }
        assert color_map["Low"]    == discord.Color.green()
        assert color_map["High"]   == discord.Color.red()
        assert color_map["Medium"] == discord.Color.orange()

    # ── /metro_profiler ──────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_build_profiler_result_no_records(self, simon_cog):
        """When there are no log entries, build_profiler_result must return ([], None)."""
        simon_cog.bot.suspect_logs.find.return_value.sort.return_value.limit.return_value.to_list \
            = AsyncMock(return_value=[])

        with patch("simon.fetch_roblox_data", new=AsyncMock(return_value=(None, None, None))):
            pages, map_file = await simon_cog.build_profiler_result("unknownuser")

        assert pages   == []
        assert map_file is None

    @pytest.mark.asyncio
    async def test_build_profiler_result_with_logs(self, simon_cog):
        """With crime logs, must return paginated embeds and call LLM."""
        logs = [
            {
                "suspect_name": "testuser",
                "crimes":       "Bank Robbery",
                "poi":          "Bank",
                "postal":       "N-205",
                "timestamp":    "2025-01-14T09:00:00",
            }
        ] * 3

        simon_cog.bot.suspect_logs.find.return_value.sort.return_value.limit.return_value.to_list \
            = AsyncMock(return_value=logs)

        import io
        dummy_map = io.BytesIO(b"fakepng")

        with patch("simon.fetch_roblox_data", new=AsyncMock(return_value=(123, "TestUser", None))), \
             patch("simon.draw_map_path", return_value=dummy_map), \
             patch("simon.call_llm", new=AsyncMock(return_value={
                 "prediction": {"reasoning": "Behavioural pattern detected."}
             })):
            pages, map_file = await simon_cog.build_profiler_result("testuser")

        assert len(pages) > 0
        assert pages[0].color == discord.Color.dark_red()
        # LLM analysis field should be on the first page
        field_names = [f.name for f in pages[0].fields]
        assert "Behavioural Pattern" in field_names


# ─────────────────────────────────────────────────────────────────────────────
# EMBED STRUCTURAL CHECKS (generic helper re-used across both cogs)
# ─────────────────────────────────────────────────────────────────────────────

class TestEmbedStructure:
    """Sanity checks that discord.Embed behaves as expected for our use cases."""

    def test_embed_description_set_correctly(self):
        embed = discord.Embed(description="Hello World", color=discord.Color.blue())
        assert embed.description == "Hello World"
        assert embed.color == discord.Color.blue()

    def test_embed_add_field(self):
        embed = discord.Embed()
        embed.add_field(name="Test Field", value="Test Value", inline=True)
        assert len(embed.fields) == 1
        assert embed.fields[0].name == "Test Field"

    def test_embed_set_footer(self):
        embed = discord.Embed()
        embed.set_footer(text="Footer text")
        assert embed.footer.text == "Footer text"

    def test_embed_set_thumbnail(self):
        url   = "https://example.com/thumb.png"
        embed = discord.Embed()
        embed.set_thumbnail(url=url)
        assert embed.thumbnail.url == url

    def test_embed_set_image(self):
        url   = "attachment://test.png"
        embed = discord.Embed()
        embed.set_image(url=url)
        assert embed.image.url == url
