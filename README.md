# Metro Predictive Policing Engine (ER:LC Simulation)

A graph-based predictive routing system that simulates suspect movement in ER:LC using Dijkstra’s algorithm, behavioural weighting, and a Gemini-powered inference layer.

It’s not magic. It’s maths pretending to be intuition.

---

## What this actually does

This system models suspect movement across a road network and predicts likely destinations based on:

- Shortest-path routing (Dijkstra via NetworkX)
- Vehicle handling characteristics
- Dynamic “chaos” modifiers (untrained units influencing behaviour)
- Crime history heatmaps (MongoDB-backed)
- LLM-based reasoning layer (Gemini API)

The result: ranked predictions of where a suspect is likely to go next, plus intercept suggestions.

---

## Core Components

### 1. Graph Engine
Built on NetworkX.

- Nodes = postal locations
- Edges = roads with distance-based weights
- Dynamic modification based on:
  - Vehicle type
  - Operational chaos factor (unWL units)

---

### 2. Crime Heatmap System
Aggregates suspect history into a simple behavioural bias model.

- Pulls logs from MongoDB
- Converts crime frequency into node-level weighting
- Biases “hot” locations during prediction

It’s basic, but it works.

---

### 3. Predictive Layer (Gemini API)
Gemini is used purely as a reasoning layer, not a calculator.

It receives:
- Top candidate routes
- Crime history summary
- Environmental modifiers

It returns:
- Primary predicted destination
- Risk level
- Tactical interpretation
- Intercept suggestions

If it fails, the system falls back to raw graph output.

---

### 4. Discord Bot Interface
Slash-command driven interface:

- `/metro_suspect_log`
  - Logs suspect activity
  - Converts natural language location into structured node data via LLM extraction

- `/metro_predict`
  - Runs full prediction pipeline
  - Outputs embed + map overlay

---

## Tech Stack

- Python 3.11+
- discord.py
- NetworkX
- MongoDB (Motor async driver)
- Pillow (map rendering)
- Google Gemini API (LLM layer)
- aiohttp (API communication)

---

## Important Design Principle

This is not an AI system in the modern “train a model” sense.

It is a layered heuristic system:

1. Physics layer (graph routing)
2. Behaviour layer (crime weighting)
3. Reasoning layer (LLM interpretation)

Each layer does one job. Badly mixing them breaks everything.

---

## Known Limitations

- Crime heatmap is frequency-based, not statistical
- LLM output is non-deterministic
- Location extraction depends heavily on prompt quality
- No long-term learning beyond MongoDB aggregation
- “Prediction” is probabilistic inference, not true forecasting

---

## Why this exists

Because routing suspects in a game is more fun when it pretends to be intelligent.

Also because pure Dijkstra doesn’t scare anyone.

---

## Setup

1. Install dependencies:
```bash
pip install discord.py networkx motor pillow aiohttp
