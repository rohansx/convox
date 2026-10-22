# ruff: noqa: E501 (Indic test strings read better unwrapped)
import json

import pytest

from convox.demo.outcome import build_prompt, parse_outcome, summarise
from convox.demo.stories import STORIES

TURNS = [
    {"side": "b", "src_text": "दो दिन से पानी नहीं आ रहा है।", "tgt_text": "ಎರಡು ದಿನದಿಂದ ನೀರು ಬರ್ತಿಲ್ಲ."},
    {"side": "a", "src_text": "ಸಂಜೆ 6 ಗಂಟೆಗೆ ಪ್ಲಂಬರ್ ಕಳುಹಿಸ್ತೀನಿ.", "tgt_text": "शाम 6 बजे plumber भेज दूँगा।"},
    {"side": "a", "src_text": "", "tgt_text": "", "backchannel": True},
]


def test_prompt_names_roles_languages_and_quotes_each_line_once():
    p = build_prompt(TURNS, STORIES["rent"], {"a": "kn-IN", "b": "hi-IN"})
    assert "Landlord" in p and "Tenant" in p and "Kannada" in p and "Hindi" in p
    assert "ಸಂಜೆ 6 ಗಂಟೆಗೆ ಪ್ಲಂಬರ್ ಕಳುಹಿಸ್ತೀನಿ." in p
    assert "शाम 6 बजे" not in p  # the speaker's own words, not the machine translation
    assert p.count("Landlord:") == 1  # the backchannel is left out


def test_prompt_without_a_story_uses_generic_roles():
    p = build_prompt(TURNS[:2], None, {"a": "kn-IN", "b": "hi-IN"})
    assert "Person A" in p and "Person B" in p


def test_parse_outcome_takes_json_even_with_surrounding_text():
    raw = 'Here you go:\n{"a": {"headline": "ಪ್ಲಂಬರ್ ಇಂದು 6ಕ್ಕೆ", "points": ["x"]}, "b": {"headline": "प्लंबर आज 6 बजे", "points": ["y", "z"]}}'
    out = parse_outcome(raw)
    assert out["a"]["headline"].startswith("ಪ್ಲಂಬರ್") and out["b"]["points"] == ["y", "z"]


@pytest.mark.parametrize("raw", ["no json here", '{"a": {"headline": 3}}', '{"a": {}, "b": {}}'])
def test_parse_outcome_rejects_bad_shapes(raw):
    assert parse_outcome(raw) is None


class FakeMessages:
    def __init__(self, text):
        self.text, self.calls = text, []

    async def create(self, **kw):
        self.calls.append(kw)
        block = type("Block", (), {"type": "text", "text": self.text})()
        return type("Resp", (), {"content": [block]})()


class FakeClient:
    def __init__(self, text):
        self.messages = FakeMessages(text)


async def test_summarise_calls_claude_once_and_returns_both_languages():
    body = {
        "a": {"headline": "ಪ್ಲಂಬರ್ 6ಕ್ಕೆ", "points": ["ಸಂಜೆ 6"]},
        "b": {"headline": "प्लंबर 6 बजे", "points": ["शाम 6"]},
    }
    client = FakeClient(json.dumps(body, ensure_ascii=False))
    out = await summarise(
        client, "claude-haiku-4-5-20251001", TURNS, STORIES["rent"], {"a": "kn-IN", "b": "hi-IN"}
    )
    assert out == body
    (call,) = client.messages.calls
    assert call["model"] == "claude-haiku-4-5-20251001" and call["max_tokens"] <= 1024


async def test_summarise_returns_none_without_turns_or_on_error():
    assert await summarise(FakeClient("{}"), "m", [], None, {"a": "kn-IN", "b": "hi-IN"}) is None

    class Boom:
        class messages:
            @staticmethod
            async def create(**kw):
                raise RuntimeError("down")

    assert await summarise(Boom(), "m", TURNS, None, {"a": "kn-IN", "b": "hi-IN"}) is None
