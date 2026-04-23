import json
import math
import networkx as nx


class ERLCGraph:
    def __init__(self, json_path: str):
        self.graph        = nx.DiGraph()
        self.nodes_data   = {}
        self.postal_nodes = {}
        self.road_graph   = {}
        self.poi_lookup   = {}  # New O(1) lookup for POIs
        self.road_geometry= {}
        self.config       = {}
        self._load_data(json_path)

    # ------------------------------------------------------------------
    # DATA LOADING
    # ------------------------------------------------------------------

    def _load_data(self, json_path: str):
        """Load JSON map data into a directed weighted graph."""
        with open(json_path, "r") as f:
            data = json.load(f)

        self.config = data.get("system_config", {})

        # --- Nodes ---
        for node_id, info in data["nodes"].items():
            if not isinstance(info, dict):
                continue
            if "x" not in info or "y" not in info:
                continue

            self.nodes_data[node_id] = info
            self.graph.add_node(
                node_id,
                x=info["x"],
                y=info["y"],
                label=info.get("label"),
                poi=info.get("poi"),
                robable=info.get("robable", False),
                type=info.get("type"),
            )
            
            # Index POIs for O(1) resolution
            if info.get("poi"):
                self.poi_lookup[info["poi"].lower().strip()] = node_id

        # --- Edges (v2 compatible loader) ---
        for edge in data.get("edges", []):
            if not isinstance(edge, dict):
                continue

            s = edge.get("source")
            t = edge.get("target")
            if not s or not t:
                continue
            if s not in data.get("nodes", {}) or t not in data.get("nodes", {}):
                continue

            n1 = data["nodes"][s]
            n2 = data["nodes"][t]
            sx, sy = n1.get("x", 0), n1.get("y", 0)
            tx, ty = n2.get("x", 0), n2.get("y", 0)
            base_cost = math.hypot(tx - sx, ty - sy)

            edge_type = edge.get("type", "local")
            road      = edge.get("road") or f"{s}__{t}"

            # road adjacency (undirected logical structure)
            if road not in self.road_graph:
                self.road_graph[road] = {}
            self.road_graph[road].setdefault(s, set()).add(t)
            self.road_graph[road].setdefault(t, set()).add(s)

            metadata      = edge.get("metadata") or {}
            postals       = metadata.get("postals") or []
            bidirectional = edge.get("bidirectional", True)

            # Index Postals to Nodes
            for p in postals:
                p_str = str(p)
                if p_str not in self.postal_nodes:
                    self.postal_nodes[p_str] = s

            def add_edge(u, v):
                self.graph.add_edge(
                    u, v,
                    road=road,
                    type=edge_type,
                    is_one_way=(not bidirectional),
                    postals=postals,
                    base_cost=base_cost,
                    weight=base_cost,
                )
                self.graph[u][v]["geometry"] = [
                    (self.nodes_data[u]["x"], self.nodes_data[u]["y"]),
                    (self.nodes_data[v]["x"], self.nodes_data[v]["y"]),
                ]

            add_edge(s, t)
            if bidirectional:
                add_edge(t, s)

        self.build_road_geometry()

    # ------------------------------------------------------------------
    # ROAD GEOMETRY
    # ------------------------------------------------------------------

    def build_road_geometry(self):
        """Build ordered polylines per road using DFS traversal."""
        self.road_geometry = {}

        for road, adjacency in self.road_graph.items():
            visited  = set()
            segments = []
            nodes    = list(adjacency.keys())
            if not nodes:
                continue

            stack = [(nodes[0], None)]
            while stack:
                node, parent = stack.pop()
                if node in visited:
                    continue
                visited.add(node)

                if parent is not None:
                    n1 = self.nodes_data.get(parent)
                    n2 = self.nodes_data.get(node)
                    if n1 and n2:
                        segments.append((n1["x"], n1["y"]))
                        segments.append((n2["x"], n2["y"]))

                for neighbor in adjacency.get(node, []):
                    if neighbor not in visited:
                        stack.append((neighbor, node))

            # deduplicate while preserving order
            seen    = set()
            cleaned = []
            for p in segments:
                if p not in seen:
                    seen.add(p)
                    cleaned.append(p)

            self.road_geometry[road] = cleaned

    # ------------------------------------------------------------------
    # NODE RESOLUTION HELPERS
    # ------------------------------------------------------------------

    def resolve_poi_to_node(self, poi_name: str):
        if not poi_name:
            return None
        poi_name   = poi_name.lower().strip()
        
        # Direct match check
        if poi_name in self.poi_lookup:
            return self.poi_lookup[poi_name]

        # Fuzzy fallback (only if direct match fails)
        return next((nid for poi, nid in self.poi_lookup.items() if poi_name in poi), None)

    def resolve_target(self, raw: str):
        """Universal resolver for nodes, postals, and POIs."""
        if not raw:
            return None
        if raw in self.graph:
            return raw
        if isinstance(raw, str) and raw.startswith("postal_"):
            return self.postal_nodes.get(raw.replace("postal_", "")) or raw
        if isinstance(raw, str) and raw.isdigit():
            return self.postal_nodes.get(raw) or f"postal_{raw}"

        poi_resolved = self.resolve_poi_to_node(raw)
        if poi_resolved:
            return poi_resolved

        # fallback: label match
        for node_id, data in self.nodes_data.items():
            if str(data.get("label", "")).lower() == str(raw).lower():
                return node_id

        return None

    # ------------------------------------------------------------------
    # WEIGHT COMPUTATION
    # ------------------------------------------------------------------

    def compute_edge_cost(
        self, base_cost: float, edge_type: str, vehicle: str, unwl_units: int
    ) -> float:
        vehicle       = (vehicle or "").lower()
        multiplier_map = self.config.get("multiplier_map", {})
        cost          = base_cost * multiplier_map.get(edge_type, 1.0)

        if vehicle == "supercar":
            if edge_type == "highway":
                cost *= 0.8
            if edge_type == "industrial":
                cost *= 1.5
        elif vehicle in ["jeep", "truck"]:
            if edge_type == "industrial":
                cost *= 0.85
            if edge_type == "highway":
                cost *= 1.1

        if unwl_units > 0:
            panic = min(unwl_units * 0.8, 0.5)
            if edge_type == "highway":
                cost *= 1.0 - panic
            if edge_type in ["local", "industrial"]:
                cost *= 1.0 + panic

        return cost

    def apply_weights(self, vehicle: str, unwl_units: int) -> nx.DiGraph:
        """Return a copy of the graph with dynamic edge weights applied."""
        G_mod = self.graph.copy()

        for u, v, data in G_mod.edges(data=True):
            # Combine postal check and weight calculation into one pass
            if data.get("type") == "postal":
                G_mod[u][v]["weight"] = 999_999
            else:
                base_cost = data.get("base_cost") or data.get("weight") or 1.0
                G_mod[u][v]["weight"] = self.compute_edge_cost(
                    base_cost, data.get("type", "local"), vehicle, unwl_units
                )

        return G_mod

    # ------------------------------------------------------------------
    # DIJKSTRA WRAPPER
    # ------------------------------------------------------------------

    def get_top_destinations(
        self, start_postal: str, G_mod: nx.DiGraph, top_n: int = 7
    ) -> list:
        """Run Dijkstra's from start_postal and return closest robable nodes."""
        if start_postal not in G_mod:
            start_postal = (
                self.postal_nodes.get(start_postal) or f"postal_{start_postal}"
            )
            if start_postal not in G_mod:
                return []

        def is_valid(n):
            return not str(n).startswith("postal_")

        lengths, paths = nx.single_source_dijkstra(
            G_mod, start_postal, weight="weight"
        )
        lengths = {k: v for k, v in lengths.items() if is_valid(k)}
        paths   = {k: p for k, p in paths.items() if is_valid(k)}

        destinations = []
        for node, distance in lengths.items():
            if node == start_postal:
                continue
            node_data = G_mod.nodes.get(node) or self.nodes_data.get(node)
            if not node_data:
                continue
            if node_data.get("robable") is not True:
                continue
            destinations.append({
                "postal":         node,
                "poi":            node_data.get("poi", "Unknown POI"),
                "robable":        True,
                "distance_score": round(distance, 2),
                "path":           paths.get(node, []),
            })

        if not destinations:
            for node, distance in list(lengths.items())[:10]:
                if node == start_postal:
                    continue
                node_data = G_mod.nodes.get(node) or {}
                destinations.append({
                    "postal":         node,
                    "poi":            node_data.get("poi", "Unknown POI"),
                    "robable":        node_data.get("robable", False),
                    "distance_score": round(distance, 2),
                    "path":           paths.get(node, []),
                })

        destinations.sort(key=lambda x: x["distance_score"])
        return destinations[:top_n]
