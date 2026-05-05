"""
heatmap.py

Kept for backward-compatibility (main.py instantiates CrimeHeatmap on the bot).
The real behavioral logic now lives in suspect_profile.SuspectProfile.

CrimeHeatmap is preserved as a no-op stub so nothing breaks on import,
but metro_predict no longer calls it — simon.py now builds a SuspectProfile
per-call instead.
"""


class CrimeHeatmap:
    """
    Legacy stub.  No longer used by metro_predict.
    Retained so main.py's `self.crime_heatmap = CrimeHeatmap()` doesn't break.
    """

    def __init__(self):
        self.weights: dict = {}

    def build_from_logs(self, logs: list):
        """No-op.  SuspectProfile handles this now."""
        pass

    def score_node(self, node_data: dict) -> float:
        """Returns neutral 1.0.  SuspectProfile.score_destination() is the real scorer."""
        return 1.0