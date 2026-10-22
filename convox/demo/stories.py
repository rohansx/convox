"""Stories for the live demo: who is on the call, in which language, and what is at stake.

A story fixes the two sides' roles and languages and gives each person a few lines to try,
so a presenter never freezes in front of a room. Side a is always listed first on the
stage. The lines are prompts for people to say, never shown as Convox output.

TODO(native review): the Kannada, Marathi and Gujarati lines were written by us (PQ5).
"""

from __future__ import annotations

from typing import Any

STORIES: dict[str, dict[str, Any]] = {
    "rent": {
        "title": "Rent",
        "headline": "The water has been off for two days",
        "stakes": "A tenant needs the landlord to send a plumber. They don't share a language.",
        "vertical": "delivery",
        "a": {
            "role": "Landlord",
            "name": "Manjunath",
            "lang": "kn-IN",
            "lines": [
                "ಹೇಳಿ, ಏನು ಸಮಸ್ಯೆ?",
                "ಇವತ್ತು ಸಂಜೆ 6 ಗಂಟೆಗೆ ಪ್ಲಂಬರ್ ಕಳುಹಿಸುತ್ತೇನೆ.",
                "ಈ ತಿಂಗಳ ಬಾಡಿಗೆ 15000 ರೂಪಾಯಿ, 5ನೇ ತಾರೀಖಿನೊಳಗೆ ಕೊಡಿ.",
            ],
        },
        "b": {
            "role": "Tenant",
            "name": "Pooja",
            "lang": "hi-IN",
            "lines": [
                "दो दिन से पानी नहीं आ रहा है।",
                "ठीक है, मैं शाम को घर पर रहूँगी।",
                "किराया मैं 5 तारीख तक दे दूँगी।",
            ],
        },
    },
    "delivery": {
        "title": "Delivery",
        "headline": "The rider is at the gate",
        "stakes": "The customer is out and the parcel needs an OTP. They don't share a language.",
        "vertical": "delivery",
        "a": {
            "role": "Customer",
            "name": "Sunita",
            "lang": "mr-IN",
            "lines": [
                "मी घरी नाही, पार्सल वॉचमनकडे द्या.",
                "ओटीपी 47296 आहे.",
                "गेट नंबर 2 वरून या.",
            ],
        },
        "b": {
            "role": "Rider",
            "name": "Ravi",
            "lang": "hi-IN",
            "lines": [
                "मैं आपकी सोसाइटी के गेट पर हूँ।",
                "ठीक है, ओटीपी बता दीजिए।",
                "पार्सल वॉचमैन को दे दिया है।",
            ],
        },
    },
    "clinic": {
        "title": "Clinic",
        "headline": "A fever that won't go away",
        "stakes": "A patient describes symptoms, a doctor prescribes. No shared language.",
        "vertical": "clinic",
        "a": {
            "role": "Patient",
            "name": "Hansaben",
            "lang": "gu-IN",
            "lines": [
                "મને ત્રણ દિવસથી તાવ છે.",
                "મને માથું પણ દુખે છે.",
                "દવા ક્યારે લેવાની?",
            ],
        },
        "b": {
            "role": "Doctor",
            "name": "Dr. Mehta",
            "lang": "en-IN",
            "lines": [
                "Since when do you have the fever?",
                "Take one tablet twice a day, after food.",
                "Come back in 3 days if it doesn't get better.",
            ],
        },
    },
}


def story(key: str | None) -> dict[str, Any] | None:
    return STORIES.get(key or "")


def side_of(key: str | None, side: str) -> dict[str, Any] | None:
    s = story(key)
    return s[side] if s and side in ("a", "b") else None
