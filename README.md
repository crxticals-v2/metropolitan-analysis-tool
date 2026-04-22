## Metropolitan Services — Simon Predictive System

A high-performance Discord bot designed for ER:LC Metropolitan Division operations, combining graph theory, behavioural modelling, and LLM-based prediction into a single operational tool.

## Overview

Metropolitan Services integrates three core systems:

* Graph Engine (NetworkX) → Computes optimal routes across ER:LC road networks
* Behavioural Layer → Adjusts predictions based on suspect history and crime patterns
* LLM Integration (Gemini API) → Produces structured, tactical predictions

The result is a system that doesn’t just track suspects — it anticipates them.

## Core Features

Predictive Policing Engine

* Uses Dijkstra-based routing with dynamic weights
* Factors in:
    * Vehicle type
    * Road hierarchy
    * unWL unit pressure (chaos factor)
* Outputs:
    * Primary & secondary targets
    * Tactical recommendations
    * Risk levels
    * Visual route overlays

## Crime Heatmap

* Aggregates MongoDB crime logs
* Generates real-time spatial intensity maps
* Highlights high-risk zones based on historical activity

## Suspect Logging System

* Stores structured crime data
* Uses LLM extraction to map vague inputs → valid nodes
* Builds long-term behavioural profiles

## Metropolitan Operational Command Tools

Includes operational Discord commands:

* /metro_predict → Run full predictive analysis
* /metro_suspect_log → Log suspect activity
* /metro_crime_heatmap → Visual intelligence overlay
* /metro_promote → Promotion system
* /metro_infract → Discipline system
* /metro_mass_shift → Division-wide mobilisation
* /metro_openings → Rank availability tracker
* /metro_log_training → Training evaluation system

## System Architecture

User Input (Discord)
        ↓
Command Handler (discord.py)
        ↓
Graph Engine (NetworkX)
        ↓
Behaviour Layer (Heatmap + History)
        ↓
LLM (Gemini API)
        ↓
Prediction Output + Map Rendering (PIL)

## Tech Stack

* Python 3.11
* discord.py (app_commands)
* NetworkX — graph computation
* MongoDB Atlas (Motor) — async database
* Pillow (PIL) — map rendering
* aiohttp — API requests
* Gemini API — structured AI prediction

## Setup

1. Clone & Install

git clone <repo>
cd AI-Suspect
pip install -r requirements.txt

2. Environment Variables (.env)

DISCORD_TOKEN=your_token
MONGO_URI=your_mongo_uri
GEMINI_API_KEY=your_api_key



3. Run

python main.py
