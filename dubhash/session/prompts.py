"""Fixed prompts the session speaks itself: the opening line (disclosure + "speak in short
sentences") and the line check ("aap line par hain?"). Each is in the listener's own
language and is synthesised with that side's TTS voice.

The opening line is ONE short sentence (about 3 s of Bulbul speech; the two-sentence
version ran 7-9 s and held up the first translation, L2/L4). The Hindi line folds the
spec's "chhote vaakya boliye" into the spec's first sentence; the Kannada line is the
spec's sentence (docs/tech-spec/security-privacy.md). Everything else is an unverified
draft.
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


_EN_NAMES = {"hi": "Hindi", "kn": "Kannada", "ta": "Tamil", "mr": "Marathi", "en": "English"}

_LANGS: dict[str, _Lang] = {
    "hi": _Lang(
        opening="aap {self} mein chhote vaakya boliye, saamne wala {other} mein sunega.",
        names={"hi": "hindi", "kn": "kannada", "ta": "tamil", "mr": "marathi", "en": "angrezi"},
        line_check="aap line par hain?",
        record=" is baatcheet ka text save kiya ja raha hai.",  # TODO(PQ5): verify wording
    ),
    # TODO(PQ5): verify. The spec's sentence; the "short sentences" hint is not in it yet.
    "kn": _Lang(
        opening="ನೀವು {self} ಮಾತನಾಡಿ, ಅವರು {other} ಕೇಳುತ್ತಾರೆ.",
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
        opening="நீங்கள் {self} பேசுங்கள், அவர்கள் {other} கேட்பார்கள்.",
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
    # TODO(PQ5): native Marathi review of the whole set ("speak short sentences in Marathi,
    # the other person will hear it in Hindi/English").
    "mr": _Lang(
        opening="तुम्ही {self} छोटी वाक्ये बोला, समोरची व्यक्ती {other} ऐकेल.",
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
    "en": _Lang(
        opening="Please speak {self} in short sentences; the other person hears {other}.",
        names=_EN_NAMES,
        line_check="Are you still on the line?",
        record=" The text of this conversation is being saved.",
    ),
}


def _base(lang: str) -> str:
    return lang.split("-", 1)[0].lower()


def opening_text(lang: str, other_lang: str, *, record: bool = False) -> str:
    """The opening line for a listener speaking `lang` whose partner speaks `other_lang`
    (BCP-47, e.g. "hi-IN"). Unknown languages fall back to English."""
    me, them = _base(lang), _base(other_lang)
    spec = _LANGS.get(me)
    if spec is None:  # English, naming the languages in English
        spec = _LANGS["en"]
    text = spec.opening.format(
        self=spec.names.get(me, me), other=spec.names.get(them, _EN_NAMES.get(them, them))
    )
    return text + (spec.record if record else "")


def line_check_text(lang: str) -> str:
    """The soft "are you on the line?" prompt after 10 s of silence."""
    spec = _LANGS.get(_base(lang))
    return spec.line_check if spec is not None else "Are you still on the line?"
