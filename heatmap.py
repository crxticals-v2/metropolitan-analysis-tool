class CrimeHeatmap:
    """Maintains per-postal crime weighting derived from MongoDB logs."""

    def __init__(self):
        self.weights: dict = {}

    def build_from_logs(self, logs: list):
        """Build a simple frequency-based heatmap from a list of log documents."""
        self.weights.clear()
        for log in logs:
            crimes = log.get("crimes", "").lower()
            for token in crimes.split():
                self.weights[token] = self.weights.get(token, 0) + 1

    def score_node(self, node_data: dict) -> float:
        """Convert POI / robability relevance into a scalar heat bias."""
        base = 1.0
        poi  = str(node_data.get("poi", "")).lower()

        if "bank" in poi:
            base += 0.6 * self.weights.get("robbery", 0)

        if node_data.get("robable"):
            base += 0.3 * sum(self.weights.values())

        return base
