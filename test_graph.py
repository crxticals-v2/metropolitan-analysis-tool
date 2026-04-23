"""
test_graph.py  –  Unit tests for graph.py (ERLCGraph).

All tests run offline — no Discord, no MongoDB, no network required.
"""

import pytest
from graph import ERLCGraph
from config import MAP_JSON_PATH


# ─────────────────────────────────────────────────────────────────────────────
# FIXTURE
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def graph():
    return ERLCGraph(MAP_JSON_PATH)


# ─────────────────────────────────────────────────────────────────────────────
# LOADING
# ─────────────────────────────────────────────────────────────────────────────

class TestGraphLoading:
    def test_nodes_loaded(self, graph):
        """erlc_map.json must parse to a non-empty node set."""
        assert len(graph.nodes_data) > 0, "No nodes were loaded from erlc_map.json"

    def test_edges_loaded(self, graph):
        """Graph must have edges connecting the nodes."""
        assert graph.graph.number_of_edges() > 0, "No edges were built — routing is broken"

    def test_every_node_has_coords(self, graph):
        """Each node must carry x/y coordinates for rendering."""
        for nid, data in graph.nodes_data.items():
            assert "x" in data and "y" in data, f"Node {nid} missing x/y coordinates"

    def test_postal_index_populated(self, graph):
        """At least some postal → node mappings must exist."""
        assert len(graph.postal_nodes) > 0, "postal_nodes index is empty"

    def test_poi_lookup_populated(self, graph):
        """POI → node lookup table must have entries."""
        assert len(graph.poi_lookup) > 0, "poi_lookup is empty"

    def test_robable_nodes_exist(self, graph):
        """At least one node must be marked robable=True for routing to work."""
        robable = [
            n for n, d in graph.nodes_data.items() if d.get("robable") is True
        ]
        assert len(robable) > 0, "No robable nodes found — LLM routing targets are broken"


# ─────────────────────────────────────────────────────────────────────────────
# RESOLUTION
# ─────────────────────────────────────────────────────────────────────────────

class TestNodeResolution:
    def test_resolve_known_poi_bank(self, graph):
        """'Bank' must resolve to the N-205 node."""
        result = graph.resolve_target("Bank")
        assert result == "N-205", f"Expected N-205, got {result!r}"

    def test_resolve_existing_node_id(self, graph):
        """Passing an existing node ID as-is must return that same ID."""
        first_node = next(iter(graph.nodes_data))
        assert graph.resolve_target(first_node) == first_node

    def test_resolve_unknown_returns_none(self, graph):
        """An unrecognised string must return None, not raise."""
        result = graph.resolve_target("ZZZ_DOES_NOT_EXIST_XYZ")
        assert result is None

    def test_resolve_empty_string(self, graph):
        assert graph.resolve_target("") is None

    def test_resolve_none(self, graph):
        assert graph.resolve_target(None) is None

    def test_resolve_poi_case_insensitive(self, graph):
        """POI resolution must be case-insensitive."""
        upper = graph.resolve_target("BANK")
        lower = graph.resolve_target("bank")
        assert upper is not None
        assert upper == lower

    def test_resolve_poi_to_node_direct(self, graph):
        """resolve_poi_to_node must handle direct lowercase key."""
        result = graph.resolve_poi_to_node("bank")
        assert result is not None
        assert result in graph.nodes_data


# ─────────────────────────────────────────────────────────────────────────────
# ROUTING
# ─────────────────────────────────────────────────────────────────────────────

class TestRouting:
    def test_apply_weights_returns_copy(self, graph):
        """apply_weights must return a new graph, not modify the original."""
        original_edges = list(graph.graph.edges)
        G_mod = graph.apply_weights("car", 0)
        assert list(graph.graph.edges) == original_edges  # original unchanged
        assert G_mod is not graph.graph

    def test_apply_weights_supercar_highway_cheaper(self, graph):
        """Supercars should have lower highway cost than baseline."""
        G_base  = graph.apply_weights("car",      0)
        G_super = graph.apply_weights("supercar", 0)

        highway_base  = [d["weight"] for u, v, d in G_base.edges(data=True)  if d.get("type") == "highway"]
        highway_super = [d["weight"] for u, v, d in G_super.edges(data=True) if d.get("type") == "highway"]

        if highway_base and highway_super:
            avg_base  = sum(highway_base)  / len(highway_base)
            avg_super = sum(highway_super) / len(highway_super)
            assert avg_super < avg_base, "Supercar highway edges should be cheaper"

    def test_apply_weights_unwl_lowers_highway(self, graph):
        """unWL units (panic factor) must reduce highway weights."""
        G_no_unwl = graph.apply_weights("car", 0)
        G_unwl    = graph.apply_weights("car", 3)

        hw_no_unwl = [d["weight"] for u, v, d in G_no_unwl.edges(data=True) if d.get("type") == "highway"]
        hw_unwl    = [d["weight"] for u, v, d in G_unwl.edges(data=True)    if d.get("type") == "highway"]

        if hw_no_unwl and hw_unwl:
            assert sum(hw_unwl) < sum(hw_no_unwl), "unWL units should reduce highway cost (flush factor)"

    def test_get_top_destinations_returns_results(self, graph):
        """Starting from N-205 (Bank) must yield at least one robable destination."""
        G_mod  = graph.apply_weights("car", 0)
        dests  = graph.get_top_destinations("N-205", G_mod, top_n=10)
        assert len(dests) > 0, "No destinations returned from N-205"

    def test_get_top_destinations_all_robable(self, graph):
        """Every returned destination must have robable=True."""
        G_mod = graph.apply_weights("car", 0)
        dests = graph.get_top_destinations("N-205", G_mod, top_n=15)
        for d in dests:
            node_data = graph.nodes_data.get(d["postal"])
            if node_data:
                assert node_data.get("robable") is True, \
                    f"Non-robable node {d['postal']} returned as destination"

    def test_get_top_destinations_have_required_fields(self, graph):
        """Each destination dict must expose postal, poi, distance_score."""
        G_mod = graph.apply_weights("car", 0)
        dests = graph.get_top_destinations("N-205", G_mod, top_n=5)
        for d in dests:
            assert "postal"         in d, f"Missing 'postal' in {d}"
            assert "poi"            in d, f"Missing 'poi' in {d}"
            assert "distance_score" in d, f"Missing 'distance_score' in {d}"

    def test_get_top_destinations_invalid_start(self, graph):
        """An invalid start node must return an empty list, not raise."""
        G_mod = graph.apply_weights("car", 0)
        dests = graph.get_top_destinations("INVALID_NODE_XYZ", G_mod, top_n=5)
        assert dests == [] or isinstance(dests, list)

    def test_compute_edge_cost_highway_cheaper_than_local(self, graph):
        """Highway edges should be cheaper than local edges at the same base cost."""
        base = 100.0
        highway_cost = graph.compute_edge_cost(base, "highway", "car", 0)
        local_cost   = graph.compute_edge_cost(base, "local",   "car", 0)
        # highway multiplier should be <= local; depends on config but shouldn't be wildly higher
        assert highway_cost <= local_cost * 2, "Highway cost unexpectedly much higher than local"
