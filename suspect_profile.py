"""
suspect_profile.py

Replaces CrimeHeatmap with a proper per-suspect behavioral model.

Key improvements over the old token-counting heatmap:
  - Markov-chain transition matrix  (what do they do AFTER what?)
  - POI type frequency tracking     (what categories do they prefer?)
  - Geographic centroid             (where do they operate?)
  - Recency weighting               (recent crimes count more)
  - Persistence to MongoDB          (profiles survive bot restarts)

Usage (drop-in at call sites):
    profile = SuspectProfile()
    profile.build_from_logs(sorted_logs, nodes_data)
    score = profile.score_destination(node_data, last_poi="Bank")
    probs = profile.get_next_poi_probabilities("Bank")
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Optional


# ──────────────────────────────────────────────────────────────────────────────
# RECENCY HELPER
# ──────────────────────────────────────────────────────────────────────────────

def _recency_weight(timestamp, half_life_days: float = 14.0) -> float:
    """
    Exponential decay weight.  A log from today = 1.0.
    A log from half_life_days ago = ~0.5.
    Ensures recent behaviour dominates older history.
    """
    if timestamp is None:
        return 0.5

    # Accept both datetime objects and ISO strings
    if isinstance(timestamp, str):
        try:
            timestamp = datetime.fromisoformat(timestamp)
        except ValueError:
            return 0.5

    # Make timezone-aware if naive
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)

    now = datetime.now(tz=timezone.utc)
    age_days = max((now - timestamp).total_seconds() / 86400.0, 0.0)
    return math.exp(-0.693 * age_days / half_life_days)   # 0.693 ≈ ln(2)


# ──────────────────────────────────────────────────────────────────────────────
# SUSPECT PROFILE
# ──────────────────────────────────────────────────────────────────────────────

class SuspectProfile:
    """
    Behavioral fingerprint for a single suspect, built from their crime logs.

    Attributes
    ----------
    poi_frequency : dict[str, float]
        Weighted count of how often this suspect targets each POI name.
        e.g.  {"Bank": 3.2, "Gas Station (City)": 1.0}

    type_frequency : dict[str, float]
        Weighted count per POI *category* (commercial, government, utility…).
        Softer signal than poi_frequency — useful for new/unseen POI types.

    transition_matrix : dict[str, dict[str, float]]
        Markov-chain table: after committing a crime at poi_A,
        what is the next POI they hit?
        e.g.  {"Bank": {"Gun Store": 2.0, "Jewelry": 1.0}}

    geo_centroid : tuple[float, float] | None
        Average (x, y) map coordinate of all logged crime locations.
        Used to bias predictions toward their home territory.

    total_weight : float
        Sum of all recency weights — proxy for "how much data do we have?"

    last_poi : str | None
        The POI from the most *recent* log entry (post sort).
        Passed into score_destination to activate transition-matrix scoring.
    """

    def __init__(self):
        self.poi_frequency:    dict[str, float] = {}
        self.type_frequency:   dict[str, float] = {}
        self.transition_matrix: dict[str, dict[str, float]] = {}
        self.geo_centroid:     tuple[float, float] | None = None
        self.total_weight:     float = 0.0
        self.total_crimes:     int   = 0
        self.last_poi:         str | None = None
        # Session-level transition matrix (within-session crime sequences only)
        self.session_transition_matrix: dict[str, dict[str, float]] = {}
        # Gang affiliation tag (populated when building from logs)
        self.gang:             str | None = None

    # ──────────────────────────────────────────────────────────────────────
    # CONFIDENCE TIER
    # ──────────────────────────────────────────────────────────────────────

    @property
    def confidence_tier(self) -> str:
        """
        Returns a human-readable confidence tier based on data volume.
        Used by the predict embed and for gating features that need enough data.
        """
        if self.total_crimes == 0:
            return "NONE"
        elif self.total_crimes < 3:
            return "LOW"
        elif self.total_crimes < 10:
            return "MEDIUM"
        else:
            return "HIGH"

    # ──────────────────────────────────────────────────────────────────────
    # BUILD
    # ──────────────────────────────────────────────────────────────────────

    def build_from_logs(self, logs: list, nodes_data: dict | None = None) -> None:
        """
        Construct the behavioral model from a list of MongoDB log documents.

        Parameters
        ----------
        logs :
            Must be sorted ascending by timestamp so transitions are correct
            (oldest → newest).  Caller is responsible for sorting.
        nodes_data :
            Optional ERLCGraph.nodes_data dict.  When provided, used to resolve
            x/y coordinates for geographic centroid even if the log itself
            doesn't carry them.
        """
        self.poi_frequency.clear()
        self.type_frequency.clear()
        self.transition_matrix.clear()
        self.total_weight = 0.0
        self.total_crimes = len(logs)
        self.last_poi     = None

        if not logs:
            return

        xs: list[float] = []
        ys: list[float] = []

        prev_poi:       str | None       = None
        prev_ts:        datetime | None  = None
        session_prev:   str | None       = None
        SESSION_GAP_H   = 2.0  # hours gap that defines a new session

        for log in logs:
            poi      = (log.get("poi") or "Unknown").strip()
            poi_type = (log.get("poi_type") or log.get("type") or "unknown").strip()
            ts       = log.get("timestamp")
            w        = _recency_weight(ts)

            # ── gang tag (take the first non-None value seen) ─────────────
            if self.gang is None and log.get("gang"):
                self.gang = log["gang"]

            # ── resolve x/y for centroid ──────────────────────────────────
            node_id = log.get("postal")
            if nodes_data and node_id and node_id in nodes_data:
                nd = nodes_data[node_id]
                xs.append(nd.get("x", 0) * w)
                ys.append(nd.get("y", 0) * w)

            # ── poi + type frequency (recency-weighted) ────────────────────
            self.poi_frequency[poi] = self.poi_frequency.get(poi, 0.0) + w
            self.type_frequency[poi_type] = (
                self.type_frequency.get(poi_type, 0.0) + w
            )
            self.total_weight += w

            # ── session boundary detection ────────────────────────────────
            # Two logs more than SESSION_GAP_H apart = new session.
            # Reset session_prev so we don't record cross-session transitions
            # in the session matrix (they pollute within-session patterns).
            is_crime = log.get("entry_type", "crime") == "crime"

            if ts is not None and prev_ts is not None:
                ts_dt    = ts if isinstance(ts, datetime) else datetime.fromisoformat(str(ts))
                prev_dt  = prev_ts if isinstance(prev_ts, datetime) else datetime.fromisoformat(str(prev_ts))
                if ts_dt.tzinfo is None: ts_dt = ts_dt.replace(tzinfo=timezone.utc)
                if prev_dt.tzinfo is None: prev_dt = prev_dt.replace(tzinfo=timezone.utc)
                gap_h = abs((ts_dt - prev_dt).total_seconds()) / 3600.0
                if gap_h > SESSION_GAP_H:
                    session_prev = None   # new session — break the chain

            # ── lifetime transition matrix ────────────────────────────────
            if prev_poi is not None and is_crime:
                if prev_poi not in self.transition_matrix:
                    self.transition_matrix[prev_poi] = {}
                self.transition_matrix[prev_poi][poi] = (
                    self.transition_matrix[prev_poi].get(poi, 0.0) + w
                )

            # ── session transition matrix (within-session only) ───────────
            if session_prev is not None and is_crime:
                if session_prev not in self.session_transition_matrix:
                    self.session_transition_matrix[session_prev] = {}
                self.session_transition_matrix[session_prev][poi] = (
                    self.session_transition_matrix[session_prev].get(poi, 0.0) + w
                )

            if is_crime:
                prev_poi     = poi
                session_prev = poi
            prev_ts = ts

        # ── last POI (most recent entry, so last in ascending list) ───────
        for log in reversed(logs):
            if log.get("entry_type", "crime") == "crime":
                self.last_poi = (log.get("poi") or "Unknown").strip()
                break

        # ── geographic centroid (weight-normalised) ───────────────────────
        if xs and ys:
            self.geo_centroid = (sum(xs) / self.total_weight,
                                 sum(ys) / self.total_weight)

    # ──────────────────────────────────────────────────────────────────────
    # QUERY HELPERS
    # ──────────────────────────────────────────────────────────────────────

    def get_next_poi_probabilities(self, from_poi: str) -> dict[str, float]:
        """
        Return a probability distribution over the next POI given *from_poi*.

        Returns empty dict when there are no transition records for from_poi.
        """
        row = self.transition_matrix.get(from_poi, {})
        total = sum(row.values())
        if total == 0:
            return {}
        return {poi: count / total for poi, count in row.items()}

    def score_destination(
        self,
        node_data:    dict,
        last_poi:     str | None = None,
        node_x:       float | None = None,
        node_y:       float | None = None,
    ) -> float:
        """
        Compute a behavioral affinity score for a candidate destination node.

        Higher score = more likely based on this suspect's recorded behaviour.
        The caller divides distance_score by this value so high-affinity,
        close targets float to the top.

        Score components
        ----------------
        1. POI name match      – heaviest weight (suspect hit this exact place before)
        2. Transition matrix   – heavy weight     (what they do AFTER last crime)
        3. POI type frequency  – medium weight    (they like commercial targets etc.)
        4. Robable bias        – light weight     (if they rob at all, robable nodes win)
        5. Geographic proximity– light weight     (stay near their operating territory)

        Parameters
        ----------
        node_data :
            nodes_data dict entry for the candidate node.
        last_poi :
            The POI name of the suspect's most recent confirmed crime location.
            Pass self.last_poi from the profile for best results.
        node_x, node_y :
            Map coordinates of the candidate node.
            Used for geographic centroid proximity scoring.
            Optional – scoring skips this component if not supplied.
        """
        score = 1.0   # neutral baseline — never returns 0 to avoid div-by-zero

        if self.total_weight <= 0:
            return score  # no data → neutral

        poi      = (node_data.get("poi") or "Unknown").strip()
        poi_type = (node_data.get("type") or "unknown").strip()

        # ── 1. Historical POI name frequency ─────────────────────────────
        poi_hits = self.poi_frequency.get(poi, 0.0)
        if poi_hits > 0:
            score += (poi_hits / self.total_weight) * 3.0

        # ── 2. Transition matrix (strongest signal) ───────────────────────
        effective_last_poi = last_poi or self.last_poi
        if effective_last_poi:
            next_probs = self.get_next_poi_probabilities(effective_last_poi)
            transition_prob = next_probs.get(poi, 0.0)
            if transition_prob > 0:
                score += transition_prob * 4.0    # biggest multiplier

        # ── 3. POI type affinity ─────────────────────────────────────────
        type_hits = self.type_frequency.get(poi_type, 0.0)
        if type_hits > 0:
            score += (type_hits / self.total_weight) * 1.5

        # ── 4. Robable bias ───────────────────────────────────────────────
        if node_data.get("robable"):
            robable_weight = sum(
                v for k, v in self.poi_frequency.items() if k != "Unknown"
            )
            if robable_weight > 0:
                score += (robable_weight / self.total_weight) * 0.8

        # ── 5. Geographic proximity to suspect's territory ────────────────
        if (
            self.geo_centroid is not None
            and node_x is not None
            and node_y is not None
        ):
            dist = math.hypot(node_x - self.geo_centroid[0],
                              node_y - self.geo_centroid[1])
            # Normalise: max useful range ~1500 map units.  Closer = higher bonus.
            proximity_bonus = max(0.0, 1.0 - dist / 1500.0) * 1.0
            score += proximity_bonus

        return round(score, 4)

    # ──────────────────────────────────────────────────────────────────────
    # SERIALISATION (for MongoDB persistence)
    # ──────────────────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Serialise the profile to a plain dict for MongoDB upsert."""
        return {
            "poi_frequency":            self.poi_frequency,
            "type_frequency":           self.type_frequency,
            "transition_matrix":        self.transition_matrix,
            "session_transition_matrix": self.session_transition_matrix,
            "geo_centroid":             list(self.geo_centroid) if self.geo_centroid else None,
            "total_weight":             self.total_weight,
            "total_crimes":             self.total_crimes,
            "last_poi":                 self.last_poi,
            "gang":                     self.gang,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SuspectProfile":
        """Restore a persisted profile from MongoDB without reprocessing all logs."""
        p = cls()
        p.poi_frequency     = data.get("poi_frequency", {})
        p.type_frequency    = data.get("type_frequency", {})
        p.transition_matrix = data.get("transition_matrix", {})
        centroid            = data.get("geo_centroid")
        p.geo_centroid      = tuple(centroid) if centroid and len(centroid) == 2 else None
        p.total_weight             = data.get("total_weight", 0.0)
        p.total_crimes             = data.get("total_crimes", 0)
        p.last_poi                 = data.get("last_poi")
        p.session_transition_matrix = data.get("session_transition_matrix", {})
        p.gang                     = data.get("gang")
        return p

    # ──────────────────────────────────────────────────────────────────────
    # DEBUG / DISPLAY
    # ──────────────────────────────────────────────────────────────────────

    def summary(self) -> str:
        """Human-readable summary for LLM prompts and debug embeds."""
        if self.total_crimes == 0:
            return "No prior history available."

        top_pois = sorted(
            self.poi_frequency.items(), key=lambda x: -x[1]
        )[:3]
        top_types = sorted(
            self.type_frequency.items(), key=lambda x: -x[1]
        )[:3]

        poi_str  = ", ".join(f"{p} ({w:.1f})" for p, w in top_pois)
        type_str = ", ".join(f"{t} ({w:.1f})" for t, w in top_types)

        next_str = "None"
        if self.last_poi:
            probs = self.get_next_poi_probabilities(self.last_poi)
            if probs:
                top_next = sorted(probs.items(), key=lambda x: -x[1])[:2]
                next_str = ", ".join(f"{p} ({v*100:.0f}%)" for p, v in top_next)

        geo_str = (
            f"({self.geo_centroid[0]:.0f}, {self.geo_centroid[1]:.0f})"
            if self.geo_centroid
            else "Unknown"
        )

        return (
            f"Total logged crimes: {self.total_crimes} | "
            f"Top targets: {poi_str} | "
            f"Preferred categories: {type_str} | "
            f"Last crime POI: {self.last_poi or 'Unknown'} | "
            f"Predicted next (Markov): {next_str} | "
            f"Operational centroid: {geo_str}"
        )