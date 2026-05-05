"""
test_utils.py  –  Unit tests for pure utility functions.

Covers:
  • simon.normalize_postal
  • simon.vehicle_label / resolve_vehicle / vehicle_speed_model / compute_eta_minutes
  • simon.VEHICLE_DB loading
  • heatmap.CrimeHeatmap
  • config loading
"""

import pytest
from heatmap import CrimeHeatmap
from simon import (
    VEHICLE_DB,
    compute_eta_minutes,
    normalize_postal,
    resolve_vehicle,
    vehicle_label,
    vehicle_speed_model,
)


# ─────────────────────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────────────────────

class TestConfig:
    def test_discord_token_present(self):
        from config import TOKEN
        assert TOKEN is not None and TOKEN != "", "DISCORD_TOKEN is missing"

    def test_mongo_uri_present(self):
        from config import MONGO_URI
        assert MONGO_URI is not None and MONGO_URI != "", "MONGO_URI is missing"

    def test_llm_key_present(self):
        from config import LLM_API_KEY
        assert LLM_API_KEY is not None, "GEMINI_API_KEY is missing"

    def test_map_json_path_is_string(self):
        from config import MAP_JSON_PATH
        assert isinstance(MAP_JSON_PATH, str) and MAP_JSON_PATH.endswith(".json")

    def test_watchlist_channel_id_is_int(self):
        from config import WATCHLIST_CHANNEL_ID
        assert isinstance(WATCHLIST_CHANNEL_ID, int)


# ─────────────────────────────────────────────────────────────────────────────
# POSTAL NORMALIZATION
# ─────────────────────────────────────────────────────────────────────────────

class TestNormalizePostal:
    @pytest.mark.parametrize("raw, expected", [
        ("205",    "N-205"),   # pure digits
        ("P205",   "N-205"),   # P-prefix format
        ("N-205",  "N-205"),   # already correct
        (" n-205 ","N-205"),   # whitespace + lowercase
        ("N-205",  "N-205"),   # uppercase passthrough
    ])
    def test_normalization(self, raw, expected):
        assert normalize_postal(raw) == expected

    def test_pure_digit_string(self):
        assert normalize_postal("999") == "N-999"

    def test_preserves_existing_n_prefix(self):
        assert normalize_postal("N-001") == "N-001"

    def test_strips_whitespace(self):
        assert normalize_postal("  300  ") == "N-300"

    def test_p_prefix_various_lengths(self):
        assert normalize_postal("P42")  == "N-42"
        assert normalize_postal("P1")   == "N-1"
        assert normalize_postal("P100") == "N-100"

    def test_fallback_extracts_digits(self):
        result = normalize_postal("postal_123")
        assert "123" in result


# ─────────────────────────────────────────────────────────────────────────────
# VEHICLE DATABASE
# ─────────────────────────────────────────────────────────────────────────────

class TestVehicleDatabase:
    def test_db_is_non_empty(self):
        assert len(VEHICLE_DB) > 0, "Vehicle DB loaded 0 vehicles"

    def test_all_vehicles_have_brand(self):
        for v in VEHICLE_DB:
            assert "brand" in v, f"Vehicle missing 'brand': {v}"

    def test_all_vehicles_have_model(self):
        for v in VEHICLE_DB:
            assert "model" in v, f"Vehicle missing 'model': {v}"

    def test_vehicle_label_format(self):
        """Label must be '{brand} {model}/{real_name}'."""
        v = VEHICLE_DB[0]
        label = vehicle_label(v)
        assert "/" in label, f"vehicle_label missing '/': {label!r}"
        assert v["brand"] in label

    def test_resolve_vehicle_returns_dict(self):
        """resolve_vehicle must return a dict for any valid label."""
        v     = VEHICLE_DB[0]
        label = vehicle_label(v)
        result = resolve_vehicle(label)
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert result == v

    def test_resolve_vehicle_invalid_returns_none(self):
        assert resolve_vehicle("NONEXISTENT VEHICLE/FAKE") is None

    def test_resolve_vehicle_empty_string(self):
        assert resolve_vehicle("") is None


# ─────────────────────────────────────────────────────────────────────────────
# VEHICLE SPEED MODEL
# ─────────────────────────────────────────────────────────────────────────────

class TestVehicleSpeedModel:
    def _car(self):
        return {"bot_category": "car", "horsepower_normalized": 5}

    def _supercar(self):
        return {"bot_category": "supercar", "horsepower_normalized": 10}

    def _truck(self):
        return {"bot_category": "truck", "horsepower_normalized": 4}

    def test_supercar_faster_than_car(self):
        sc_speed = vehicle_speed_model(self._supercar(), "mixed")
        ca_speed = vehicle_speed_model(self._car(), "mixed")
        assert sc_speed > ca_speed, "Supercar should be faster than base car"

    def test_truck_slower_than_car(self):
        tr_speed = vehicle_speed_model(self._truck(), "mixed")
        ca_speed = vehicle_speed_model(self._car(), "mixed")
        assert tr_speed < ca_speed, "Truck should be slower than base car"

    def test_highway_faster_than_city(self):
        hw = vehicle_speed_model(self._car(), "highway")
        cy = vehicle_speed_model(self._car(), "city")
        assert hw > cy, "Highway context should yield higher speed than city"

    def test_speed_always_positive(self):
        for ctx in ("highway", "city", "mixed"):
            assert vehicle_speed_model(self._car(), ctx) > 0

    def test_unknown_context_falls_back_to_mixed(self):
        s_unknown = vehicle_speed_model(self._car(), "UNKNOWN_CONTEXT")
        s_mixed   = vehicle_speed_model(self._car(), "mixed")
        assert s_unknown == s_mixed

    def test_zero_hp_uses_floor(self):
        """A vehicle with hp_normalized=0 should still have positive speed."""
        v = {"bot_category": "car", "horsepower_normalized": 0}
        assert vehicle_speed_model(v, "mixed") > 0

    def test_high_hp_increases_speed(self):
        low_hp  = {"bot_category": "car", "horsepower_normalized": 1}
        high_hp = {"bot_category": "car", "horsepower_normalized": 10}
        assert vehicle_speed_model(high_hp, "mixed") > vehicle_speed_model(low_hp, "mixed")


# ─────────────────────────────────────────────────────────────────────────────
# ETA COMPUTATION
# ─────────────────────────────────────────────────────────────────────────────

class TestComputeETA:
    BASE_VEHICLE = {"bot_category": "car", "horsepower_normalized": 5}

    def test_short_drive_under_2_minutes(self):
        """50-pixel distance in city should be ≤ 2 minutes (337× scaling fix)."""
        eta = compute_eta_minutes(50.0, self.BASE_VEHICLE, "city")
        assert 0 <= eta <= 2, f"ETA for short drive: {eta} min — scaling may be off"

    def test_zero_distance_is_zero(self):
        eta = compute_eta_minutes(0.0, self.BASE_VEHICLE, "city")
        assert eta == 0

    def test_eta_non_negative(self):
        for dist in (0, 10, 50, 200, 1000):
            eta = compute_eta_minutes(float(dist), self.BASE_VEHICLE, "mixed")
            assert eta >= 0, f"Negative ETA for distance {dist}: {eta}"

    def test_longer_distance_longer_eta(self):
        short = compute_eta_minutes(50.0,  self.BASE_VEHICLE, "mixed")
        long_ = compute_eta_minutes(500.0, self.BASE_VEHICLE, "mixed")
        assert long_ >= short, "Longer distance should produce longer or equal ETA"

    def test_supercar_eta_lower_than_truck(self):
        supercar = {"bot_category": "supercar", "horsepower_normalized": 10}
        truck    = {"bot_category": "truck",    "horsepower_normalized": 4}
        eta_sc   = compute_eta_minutes(200.0, supercar, "highway")
        eta_tr   = compute_eta_minutes(200.0, truck,    "highway")
        assert eta_sc <= eta_tr, "Supercar ETA should be ≤ truck ETA on same distance"

    def test_returns_integer(self):
        eta = compute_eta_minutes(100.0, self.BASE_VEHICLE, "mixed")
        assert isinstance(eta, int), f"Expected int, got {type(eta)}"


# ─────────────────────────────────────────────────────────────────────────────
# CRIME HEATMAP
# ─────────────────────────────────────────────────────────────────────────────

class TestCrimeHeatmap:
    def test_empty_heatmap_scores_baseline(self):
        hm = CrimeHeatmap()
        node = {"poi": "Warehouse", "robable": True}
        score = hm.score_node(node)
        assert score >= 1.0, "Empty heatmap baseline score should be >= 1.0"

    def test_bank_robbery_boosts_bank_score(self):
        hm = CrimeHeatmap()
        logs = [
            {"crimes": "robbery bank heist"},
            {"crimes": "robbery armed"},
            {"crimes": "robbery jewelry"},
        ]
        hm.build_from_logs(logs)
        bank_node = {"poi": "Bank", "robable": True}
        other_node = {"poi": "Warehouse", "robable": True}
        bank_score  = hm.score_node(bank_node)
        other_score = hm.score_node(other_node)
        assert bank_score > other_score, \
            "Bank node should score higher when 'robbery' appears in logs"

    def test_build_clears_old_weights(self):
        hm = CrimeHeatmap()
        hm.build_from_logs([{"crimes": "robbery"}])
        old_keys = set(hm.weights.keys())
        hm.build_from_logs([{"crimes": "kidnapping"}])
        assert "robbery" not in hm.weights, "Old weights should be cleared on rebuild"
        assert "kidnapping" in hm.weights

    def test_empty_logs_zero_weights(self):
        hm = CrimeHeatmap()
        hm.build_from_logs([])
        assert hm.weights == {}

    def test_robable_node_scores_higher_than_non_robable(self):
        hm = CrimeHeatmap()
        hm.build_from_logs([{"crimes": "robbery"}])
        rob   = {"poi": "Store", "robable": True}
        no_rob= {"poi": "Store", "robable": False}
        assert hm.score_node(rob) >= hm.score_node(no_rob)

    def test_score_never_below_one(self):
        hm = CrimeHeatmap()
        node = {"poi": "Random Place", "robable": False}
        assert hm.score_node(node) >= 1.0
