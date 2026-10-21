"""Fixed prompts the session speaks itself: the opening line (disclosure + "speak in short
sentences") and the line check ("aap line par hain?"). Each is in the listener's own
language and is synthesised with that side's TTS voice.

Only the Hindi opening line comes from the spec (docs/tech-spec/security-privacy.md), and
the Kannada opening's first sentence. Everything else here is an unverified draft.
TODO(PQ5): native-speaker and counsel review of every kn/ta/mr string below, and of the
proposed "automatic interpreter / press 0" disclosure sentence (open question G17).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class _Lang:
    opening: str  # {self} and {other}: this language's own forms of the language names
    names: dict[str, str]
    line_check: str
    record: str  # appended when the call's text is being kept (record=true)


_LANGS: dict[str, _Lang] = {
    "hi": _Lang(
        opening="aap {self} mein boliye, saamne wala {other} mein sunega. chhote vaakya boliye.",
        names={"hi": "hindi", "kn": "kannada", "ta": "tamil", "mr": "marathi", "en": "angrezi"},
        line_check="aap line par hain?",
        record=" is baatcheet ka text save kiya ja raha hai.",  # TODO(PQ5): verify wording
    ),
    # TODO(PQ5): verify. First sentence is the spec's; the second is a draft.
    "kn": _Lang(
        opening="ನೀವು {self} ಮಾತನಾಡಿ, ಅವರು {other} ಕೇಳುತ್ತಾರೆ. ಚಿಕ್ಕ ವಾಕ್ಯಗಳಲ್ಲಿ ಮಾತನಾಡಿ.",
        names={
            "kn": "ಕನ್ನಡದಲ್ಲಿ",
            "hi": "ಹಿಂದಿಯಲ್ಲಿ",
            "ta": "ತಮಿಳಿನಲ್ಲಿ",
            "mr": "ಮರಾಠಿಯಲ್ಲಿ",
            "en": "ಇಂಗ್ಲಿಷ್‌ನಲ್ಲಿ",
        },
        line_check="ನೀವು ಲೈನ್‌ನಲ್ಲಿ ಇದ್ದೀರಾ?",
        record=" ಈ ಸಂಭಾಷಣೆಯ ಪಠ್ಯವನ್ನು ಉಳಿಸಲಾಗುತ್ತಿದೆ.",
    ),
    # TODO(PQ5): verify the whole Tamil set.
    "ta": _Lang(
        opening="நீங்கள் {self} பேசுங்கள், அவர்கள் {other} கேட்பார்கள். சிறிய வாக்கியங்களில் பேசுங்கள்.",
        names={
            "ta": "தமிழில்",
            "hi": "இந்தியில்",
            "kn": "கன்னடத்தில்",
            "mr": "மராத்தியில்",
            "en": "ஆங்கிலத்தில்",
        },
        line_check="நீங்கள் லைனில் இருக்கிறீர்களா?",
        record=" இந்த உரையாடலின் உரை சேமிக்கப்படுகிறது.",
    ),
    # TODO(PQ5): verify the whole Marathi set.
    "mr": _Lang(
        opening="तुम्ही {self} बोला, समोरची व्यक्ती {other} ऐकेल. छोटी वाक्ये बोला.",
        names={
            "mr": "मराठीत",
            "hi": "हिंदीत",
            "kn": "कन्नडमध्ये",
            "ta": "तमिळमध्ये",
            "en": "इंग्रजीत",
        },
        line_check="तुम्ही लाइनवर आहात का?",
        record=" या संभाषणाचा मजकूर जतन केला जात आहे.",
    ),
}

_EN_NAMES = {"hi": "Hindi", "kn": "Kannada", "ta": "Tamil", "mr": "Marathi", "en": "English"}


def _base(lang: str) -> str:
    return lang.split("-", 1)[0].lower()


def opening_text(lang: str, other_lang: str, *, record: bool = False) -> str:
    """The opening line for a listener speaking `lang` whose partner speaks `other_lang`
    (BCP-47, e.g. "hi-IN"). Unknown languages fall back to English."""
    me, them = _base(lang), _base(other_lang)
    spec = _LANGS.get(me)
    if spec is None:
        text = (
            f"Please speak in {_EN_NAMES.get(me, me)}; the other person will hear "
            f"{_EN_NAMES.get(them, them)}. Use short sentences."
        )
        return text + (" The text of this conversation is being saved." if record else "")
    text = spec.opening.format(
        self=spec.names.get(me, me), other=spec.names.get(them, _EN_NAMES.get(them, them))
    )
    return text + (spec.record if record else "")


def line_check_text(lang: str) -> str:
    """The soft "are you on the line?" prompt after 10 s of silence."""
    spec = _LANGS.get(_base(lang))
    return spec.line_check if spec is not None else "Are you still on the line?"
