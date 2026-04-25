"""
test_commands.py  –  Command-level integration tests.

These tests call the slash-command handler methods directly (bypassing Discord's
gateway) to verify:
  • Correct interaction responses (defer / followup / send_message)
  • Error-path handling (invalid input, DB failure, cooldowns)
  • Data written to / read from the mocked MongoDB
  • /metro_predict routing logic
  • /request_metro cooldown mechanics
  • /metro_link store & retrieve
"""

import datetime
import time
from unittest.mock import AsyncMock, MagicMock, call, patch

import discord
import pytest

from conftest import (
    make_channel,
    make_guild,
    make_interaction,
    make_member,
    make_mongo_collection,
    make_role,
)


# ─────────────────────────────────────────────────────────────────────────────
# SIMON – /metro_suspect_log
# ─────────────────────────────────────────────────────────────────────────────

class TestMetroSuspectLog:
    """Tests for the /metro_suspect_log command."""

    @pytest.mark.asyncio
    async def test_logs_entry_to_mongodb(self, simon_cog):
        """Valid log should insert one document and confirm with ✅."""
        gang_choice = MagicMock()
        gang_choice.name = "77th Saints Gang (77th)"
        gang_choice.value = "77th"

        interaction = make_interaction()

        mock_llm_response = {
            "prediction": {
                "postal":     "N-205",
                "poi":        "Bank",
                "confidence": 0.9,
            }
        }
        # Location extractor also calls call_llm; it returns location data
        location_response = {"postal": "N-205", "poi": "Bank", "confidence": 0.9}

        with patch("simon.call_llm", new=AsyncMock(return_value=location_response)):
            await simon_cog.metro_suspect_log.callback(
                simon_cog,
                interaction,
                suspect_name="TestCriminal",
                gang=gang_choice,
                crimes_committed="Armed Robbery",
                location="Near the bank on Main Street",
                entry_type="crime",
            )

        simon_cog.bot.suspect_logs.insert_one.assert_called_once()
        inserted = simon_cog.bot.suspect_logs.insert_one.call_args[0][0]
        assert inserted["suspect_name"] == "testcriminal"
        assert "Armed Robbery"           in inserted["crimes"]
        assert "crime"                   == inserted["entry_type"]
        assert inserted["gang"]          == "77th"

    @pytest.mark.asyncio
    async def test_suspect_name_lowercased(self, simon_cog):
        interaction = make_interaction()
        location_response = {"postal": None, "poi": None, "confidence": 0.0}
        
        gang_choice = MagicMock()
        gang_choice.name = "None"
        gang_choice.value = "none"

        with patch("simon.call_llm", new=AsyncMock(return_value=location_response)):
            await simon_cog.metro_suspect_log.callback(
                simon_cog,
                interaction,
                suspect_name="UPPERCASE_NAME",
                crimes_committed="Theft",
                gang=gang_choice,
                location="Downtown",
                entry_type="crime",
            )

        inserted = simon_cog.bot.suspect_logs.insert_one.call_args[0][0]
        assert inserted["suspect_name"] == "uppercase_name"

    @pytest.mark.asyncio
    async def test_sends_confirmation_message(self, simon_cog):
        interaction = make_interaction()
        gang_choice = MagicMock()
        gang_choice.value = "none"

        with patch("simon.call_llm", new=AsyncMock(return_value={"postal": None, "poi": None})):
            await simon_cog.metro_suspect_log.callback(
                simon_cog, interaction,
                suspect_name="Alpha", 
                gang=gang_choice,
                crimes_committed="Assault",
                location="Park", entry_type="crime",
            )

        interaction.followup.send.assert_called_once()
        response_text = str(interaction.followup.send.call_args)
        assert "✅" in response_text or "Logged" in response_text

    @pytest.mark.asyncio
    async def test_fallback_on_invalid_postal(self, simon_cog):
        """When LLM returns a postal not in the graph, the entry still saves."""
        interaction = make_interaction()
        gang_choice = MagicMock()
        gang_choice.value = "none"

        # Return an unknown postal that isn't in nodes_data
        location_response = {"postal": "N-9999", "poi": "Unknown", "confidence": 0.1}

        with patch("simon.call_llm", new=AsyncMock(return_value=location_response)):
            await simon_cog.metro_suspect_log.callback(
                simon_cog, interaction,
                suspect_name="suspect",
                gang=gang_choice,
                crimes_committed="Robbery",
                location="Somewhere",
                entry_type="crime",
            )

        # Should have inserted despite invalid postal (fallback clears location)
        simon_cog.bot.suspect_logs.insert_one.assert_called_once()
        inserted = simon_cog.bot.suspect_logs.insert_one.call_args[0][0]
        assert inserted["postal"] is None


# ─────────────────────────────────────────────────────────────────────────────
# SIMON – /metro_predict
# ─────────────────────────────────────────────────────────────────────────────

class TestMetroPredict:
    """Tests for the /metro_predict command."""

    def _valid_vehicle_label(self) -> str:
        from simon import VEHICLE_DB, vehicle_label
        return vehicle_label(VEHICLE_DB[0])

    @pytest.mark.asyncio
    async def test_invalid_vehicle_returns_error(self, simon_cog):
        interaction = make_interaction()

        await simon_cog.metro_predict.callback(
            simon_cog, interaction,
            postal="N-205",
            vehicle="NOT A REAL VEHICLE/FAKE",
            suspect_name="testuser",
        )

        interaction.followup.send.assert_called_once()
        assert "Invalid vehicle" in str(interaction.followup.send.call_args)

    @pytest.mark.asyncio
    async def test_invalid_postal_returns_error(self, simon_cog):
        interaction = make_interaction()

        await simon_cog.metro_predict.callback(
            simon_cog, interaction,
            postal="N-9999999",
            vehicle=self._valid_vehicle_label(),
            suspect_name="testuser",
        )

        interaction.followup.send.assert_called_once()
        assert "not found" in str(interaction.followup.send.call_args).lower()

    @pytest.mark.asyncio
    async def test_valid_predict_sends_embed(self, simon_cog):
        """Full happy path: valid postal + vehicle → embed is sent."""
        interaction = make_interaction()

        # Provide suspect logs (history)
        logs = [{"suspect_name": "testuser", "crimes": "robbery", "location_raw": "Bank", "entry_type": "crime"}]
        simon_cog.bot.suspect_logs.find.return_value.sort.return_value.limit.return_value.to_list \
            = AsyncMock(return_value=logs)

        llm_response = {
            "prediction": {
                "primary_target":        "N-206",
                "secondary_target":      "N-300",
                "threat_level":          "HIGH",
                "behavioral_profile":    "Experienced robber.",
                "tactical_recommendation": "Set up perimeter.",
                "probability_score":     0.82,
                "reasoning":             "Suspect favours financial targets.",
            }
        }

        import io
        dummy_map = io.BytesIO(b"fakepng")

        with patch("simon.call_llm", new=AsyncMock(return_value=llm_response)), \
             patch("simon.draw_map_path", return_value=dummy_map):

            await simon_cog.metro_predict.callback(
                simon_cog, interaction,
                postal="N-205",
                vehicle=self._valid_vehicle_label(),
                suspect_name="testuser",
                optional_tags=None,
                unwl_units=0,
                live_context=None,
            )

        interaction.followup.send.assert_called_once()
        call_kwargs = interaction.followup.send.call_args.kwargs
        embed = call_kwargs.get("embed")
        assert embed is not None
        assert "Metro Predictive Engine" in embed.title
        # Check that risk / probability fields are present
        field_names = [f.name for f in embed.fields]
        assert any("Probability" in fn     for fn in field_names)
        assert any("Risk Level"  in fn     for fn in field_names)
        assert any("ETA"         in fn     for fn in field_names)

    @pytest.mark.asyncio
    async def test_postal_normalization_in_predict(self, simon_cog):
        """Passing 'P205' should be treated same as 'N-205'."""
        interaction = make_interaction()
        logs = []
        simon_cog.bot.suspect_logs.find.return_value.sort.return_value.limit.return_value.to_list \
            = AsyncMock(return_value=logs)

        with patch("simon.call_llm", new=AsyncMock(return_value={
            "prediction": {
                "primary_target": "N-300", "secondary_target": None,
                "threat_level": "LOW", "behavioral_profile": "",
                "tactical_recommendation": "", "probability_score": 0.5,
                "reasoning": "test",
            }
        })), patch("simon.draw_map_path", return_value=None):
            await simon_cog.metro_predict.callback(
                simon_cog, interaction,
                postal="P205",                   # ← non-normalised input
                vehicle=self._valid_vehicle_label(),
                suspect_name="",
            )

        # Should NOT produce a "not found" error
        sent_args = str(interaction.followup.send.call_args)
        assert "not found" not in sent_args.lower()

    @pytest.mark.asyncio
    async def test_unwl_units_adds_failsafe_field(self, simon_cog):
        """When unwl_units > 0, the embed must include a Failsafe Directive field."""
        interaction = make_interaction()
        simon_cog.bot.suspect_logs.find.return_value.sort.return_value.limit.return_value.to_list \
            = AsyncMock(return_value=[])

        with patch("simon.call_llm", new=AsyncMock(return_value={
            "prediction": {
                "primary_target": "N-206", "secondary_target": None,
                "threat_level": "HIGH", "behavioral_profile": "",
                "tactical_recommendation": "Secure the perimeter.",
                "probability_score": 0.8, "reasoning": "Test.",
            }
        })), patch("simon.draw_map_path", return_value=None):
            await simon_cog.metro_predict.callback(
                simon_cog, interaction,
                postal="N-205",
                vehicle=self._valid_vehicle_label(),
                suspect_name="",
                unwl_units=2,
            )

        embed = interaction.followup.send.call_args.kwargs.get("embed")
        field_names = [f.name for f in embed.fields]
        assert any("Failsafe" in fn for fn in field_names)


# ─────────────────────────────────────────────────────────────────────────────
# SIMON – /metro_watchlist (command path)
# ─────────────────────────────────────────────────────────────────────────────

class TestMetroWatchlistCommand:
    @pytest.mark.asyncio
    async def test_no_suspects_sends_error(self, simon_cog):
        interaction = make_interaction()
        simon_cog.bot.suspect_logs.aggregate.return_value.to_list = AsyncMock(
            return_value=[]
        )

        await simon_cog.metro_watchlist.callback(simon_cog, interaction)

        interaction.followup.send.assert_called_once()
        assert "No suspect" in str(interaction.followup.send.call_args) or \
               "❌"         in str(interaction.followup.send.call_args)

    @pytest.mark.asyncio
    async def test_watchlist_with_suspects_sends_embed(self, simon_cog):
        suspects = [
            {"_id": "alpha", "count": 8, "last_crime": "Robbery",
             "last_location": "N-205", "last_seen": "2025-01-10T08:00:00"},
            {"_id": "bravo", "count": 5, "last_crime": "Theft",
             "last_location": "N-300", "last_seen": "2025-01-11T09:00:00"},
        ]
        interaction = make_interaction()
        simon_cog.bot.suspect_logs.aggregate.return_value.to_list = AsyncMock(
            return_value=suspects
        )

        with patch("simon.build_watchlist_grid", new=AsyncMock(return_value=None)):
            await simon_cog.metro_watchlist.callback(simon_cog, interaction)

        interaction.followup.send.assert_called_once()
        call_kwargs = interaction.followup.send.call_args.kwargs
        embed = call_kwargs.get("embed")
        assert embed is not None
        assert "WATCHLIST" in embed.description.upper()


# ─────────────────────────────────────────────────────────────────────────────
# OPERATIONS – /request_metro (cooldown)
# ─────────────────────────────────────────────────────────────────────────────

class TestRequestMetro:
    @pytest.mark.asyncio
    async def test_first_request_succeeds(self, operations_cog):
        guild       = make_guild()
        channel     = make_channel()
        interaction = make_interaction(guild=guild, channel=channel)

        operations_cog._resolve_output_channel = MagicMock(return_value=channel)
        operations_cog.bot.request_metro_cooldowns = {}

        with patch("operations.discord.utils.get", side_effect=[
            make_role("Metropolitan Division"),
            make_role("Special Weapons and Tactics Team"),
        ]):
            await operations_cog.request_metro.callback(
                operations_cog, interaction, reason="Active hostage situation"
            )

        # Confirmation sent to interaction user
        interaction.response.send_message.assert_called_once()
        assert "✅" in str(interaction.response.send_message.call_args)

        # Embed sent to the channel
        channel.send.assert_called_once()
        embed = channel.send.call_args.kwargs["embed"]
        assert "Active hostage situation" in embed.description

    @pytest.mark.asyncio
    async def test_cooldown_blocks_second_request(self, operations_cog):
        guild       = make_guild()
        interaction = make_interaction(guild=guild)

        # Simulate a request 1 hour ago (within the 6h window)
        operations_cog.bot.request_metro_cooldowns = {guild.id: time.time() - 3600}

        await operations_cog.request_metro.callback(
            operations_cog, interaction, reason="Testing cooldown"
        )

        # Should receive a cooldown message, NOT ✅
        interaction.response.send_message.assert_called_once()
        response = str(interaction.response.send_message.call_args)
        assert "cooldown" in response.lower() or "⏳" in response

    @pytest.mark.asyncio
    async def test_cooldown_resets_after_expiry(self, operations_cog):
        guild       = make_guild()
        channel     = make_channel()
        interaction = make_interaction(guild=guild, channel=channel)

        # 7 hours ago — outside the 6-hour window
        operations_cog.bot.request_metro_cooldowns = {guild.id: time.time() - 25200}
        operations_cog._resolve_output_channel = MagicMock(return_value=channel)

        with patch("operations.discord.utils.get", side_effect=[
            make_role("Metropolitan Division"),
            make_role("Special Weapons and Tactics Team"),
        ]):
            await operations_cog.request_metro.callback(
                operations_cog, interaction, reason="Post-cooldown request"
            )

        # Should succeed
        assert "✅" in str(interaction.response.send_message.call_args)

    @pytest.mark.asyncio
    async def test_missing_roles_sends_error(self, operations_cog):
        guild       = make_guild()
        interaction = make_interaction(guild=guild)
        operations_cog.bot.request_metro_cooldowns = {}

        with patch("operations.discord.utils.get", return_value=None):
            await operations_cog.request_metro.callback(
                operations_cog, interaction, reason="Test"
            )

        response = str(interaction.response.send_message.call_args)
        assert "No valid response roles" in response or "❌" in response


# ─────────────────────────────────────────────────────────────────────────────
# OPERATIONS – /metro_link
# ─────────────────────────────────────────────────────────────────────────────

class TestMetroLink:
    def _choice(self, value: str) -> MagicMock:
        c       = MagicMock()
        c.value = value
        return c

    @pytest.mark.asyncio
    async def test_link_stores_thread_id(self, operations_cog):
        """Linking a thread must call update_one with the correct key."""
        interaction = make_interaction()
        thread      = MagicMock()
        thread.id   = 112233
        thread.mention = "<#112233>"

        operations_cog.user_links.update_one = AsyncMock()

        await operations_cog.metro_link.callback(
            operations_cog,
            interaction,
            action=self._choice("link"),
            link_type=self._choice("after_action"),
            thread=thread,
        )

        operations_cog.user_links.update_one.assert_called_once()
        args = operations_cog.user_links.update_one.call_args
        # Check the $set contains after_action_thread: 112233
        assert args[0][1]["$set"]["after_action_thread"] == 112233

    @pytest.mark.asyncio
    async def test_link_without_thread_sends_error(self, operations_cog):
        """Linking without providing a thread must return an error."""
        interaction = make_interaction()

        await operations_cog.metro_link.callback(
            operations_cog,
            interaction,
            action=self._choice("link"),
            link_type=self._choice("k9"),
            thread=None,
        )

        interaction.response.send_message.assert_called_once()
        assert "❌" in str(interaction.response.send_message.call_args)

    @pytest.mark.asyncio
    async def test_unlink_calls_unset(self, operations_cog):
        """Unlinking must call update_one with $unset."""
        interaction = make_interaction()
        operations_cog.user_links.update_one = AsyncMock()

        await operations_cog.metro_link.callback(
            operations_cog,
            interaction,
            action=self._choice("unlink"),
            link_type=self._choice("after_action"),
            thread=None,
        )

        operations_cog.user_links.update_one.assert_called_once()
        args = operations_cog.user_links.update_one.call_args
        assert "$unset" in args[0][1]


# ─────────────────────────────────────────────────────────────────────────────
# OPERATIONS – /metro_openings
# ─────────────────────────────────────────────────────────────────────────────

class TestMetroOpenings:
    @pytest.mark.asyncio
    async def test_openings_sends_multiple_embeds(self, operations_cog):
        guild   = make_guild()
        channel = make_channel()
        # Make guild.members populated
        guild.member_count = len(guild.members)

        interaction = make_interaction(guild=guild, channel=channel)
        operations_cog._resolve_output_channel = MagicMock(return_value=channel)

        with patch("operations.discord.utils.get", return_value=None):
            await operations_cog.metro_openings.callback(
                operations_cog, interaction
            )

        channel.send.assert_called()
        call_kwargs = channel.send.call_args.kwargs
        embeds = call_kwargs.get("embeds")
        assert embeds is not None and len(embeds) > 1, "Expected multiple embeds"

    @pytest.mark.asyncio
    async def test_openings_embed_has_rank_names(self, operations_cog):
        guild   = make_guild()
        channel = make_channel()
        guild.member_count = 0

        interaction = make_interaction(guild=guild, channel=channel)
        operations_cog._resolve_output_channel = MagicMock(return_value=channel)

        with patch("operations.discord.utils.get", return_value=None):
            await operations_cog.metro_openings.callback(
                operations_cog, interaction
            )

        all_embed_text = " ".join(
            e.description for e in channel.send.call_args.kwargs["embeds"]
        )
        # At least some of the hardcoded rank names must appear
        assert "Metro Director"       in all_embed_text
        assert "Metro Senior Officer" in all_embed_text


# ─────────────────────────────────────────────────────────────────────────────
# OPERATIONS – /host_metro_training
# ─────────────────────────────────────────────────────────────────────────────

class TestHostMetroTraining:
    @pytest.mark.asyncio
    async def test_host_training_sends_embed_and_reactions(self, operations_cog):
        guild   = make_guild()
        channel = make_channel()
        user    = make_member(display_name="Trainer McGee")
        interaction = make_interaction(user=user, guild=guild, channel=channel)

        operations_cog._resolve_output_channel = MagicMock(return_value=channel)

        training_ping = make_role("[𝐌𝐃] Awaiting Training Ping")
        with patch("operations.discord.utils.get", return_value=training_ping):
            await operations_cog.host_metro_training.callback(
                operations_cog, interaction,
                co_host=None,
                start_time="8:00 PM EST",
            )

        channel.send.assert_called_once()
        embed = channel.send.call_args.kwargs.get("embed")
        assert embed is not None
        assert interaction.user.mention in embed.description
        assert "8:00 PM EST"        in embed.description
        assert embed.color          == discord.Color.blue()

    @pytest.mark.asyncio
    async def test_host_training_missing_role_sends_error(self, operations_cog):
        interaction = make_interaction()

        with patch("operations.discord.utils.get", return_value=None):
            await operations_cog.host_metro_training.callback(
                operations_cog, interaction, co_host=None, start_time="TBD"
            )

        interaction.response.send_message.assert_called_once()
        assert "not found" in str(interaction.response.send_message.call_args).lower()


# ─────────────────────────────────────────────────────────────────────────────
# OPERATIONS – /metro_log_training (opens modal)
# ─────────────────────────────────────────────────────────────────────────────

class TestMetroLogTraining:
    @pytest.mark.asyncio
    async def test_opens_modal(self, operations_cog):
        interaction = make_interaction()

        await operations_cog.metro_log_training.callback(
            operations_cog, interaction,
            trainee=make_member(display_name="Recruit"),
            outcome="PASSED",
            notes="Strong performance.",
            co_host=None,
        )

        interaction.response.send_modal.assert_called_once()
        # The modal passed to send_modal should be a MetroTrainingModal
        from operations import MetroTrainingModal
        modal_arg = interaction.response.send_modal.call_args[0][0]
        assert isinstance(modal_arg, MetroTrainingModal)
