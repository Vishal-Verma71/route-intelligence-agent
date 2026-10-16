"""Customs clearance model for international shipments.

Typical clearance times per destination are illustrative business assumptions (DataCo has
no customs data); active customs holds from the disruption feed add to them. The agent can
recommend an expedited customs broker or switching to express air when a shipment would
otherwise miss its promised date.
"""
from __future__ import annotations

# Typical import clearance, hours (illustrative assumptions by destination, Spanish DataCo names).
CLEARANCE_H = {
    "Australia": 24, "Nueva Zelanda": 24, "Japón": 24, "Corea del Sur": 30, "Singapur": 12,
    "China": 48, "Hong Kong": 18, "Taiwán": 30, "Malasia": 36, "Tailandia": 48, "Vietnam": 60,
    "Indonesia": 72, "Filipinas": 72, "India": 72, "Pakistán": 96, "Bangladés": 96, "Sri Lanka": 72,
    "Canada": 12, "México": 36, "Reino Unido": 24, "Alemania": 24, "Francia": 24,
}
DEFAULT_CLEARANCE_H = 48

# Options the agent can choose between for an international shipment.
OPTIONS = {
    "standard": {"label": "Standard air freight, standard clearance", "air_kmh": 750, "handling_h": 30,
                 "clearance_factor": 1.0, "extra_cost": 0.0},
    "expedited_broker": {"label": "Standard air freight + expedited customs broker", "air_kmh": 750,
                         "handling_h": 30, "clearance_factor": 0.45, "extra_cost": 180.0},
    "express_air": {"label": "Switch to express air (priority handling + broker)", "air_kmh": 820,
                    "handling_h": 14, "clearance_factor": 0.40, "extra_cost": 520.0},
}
LAST_MILE_H = 8


def clearance_hours(country: str) -> int:
    return CLEARANCE_H.get(country, DEFAULT_CLEARANCE_H)
