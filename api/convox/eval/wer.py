"""Word and character error rate against known ground truth.

This is the measurement the architecture exists to make possible. Because Convox
generates the caller's speech from text, the reference transcript is known
exactly, before any audio exists — so recognition accuracy is a diff against
truth rather than an estimate, and errors can be located to the specific word
that was lost.

Entity error rate is reported separately and matters more than the headline
number: 5% WER concentrated entirely in a phone number is a broken agent, while
15% WER spread across filler words is fine. A single figure hides that.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from convox.eval import normalize


@dataclass
class Alignment:
    hits: int = 0
    substitutions: int = 0
    deletions: int = 0
    insertions: int = 0
    #: (operation, reference token, hypothesis token) for every edit.
    edits: list[tuple[str, str | None, str | None]] = field(default_factory=list)

    @property
    def reference_length(self) -> int:
        return self.hits + self.substitutions + self.deletions

    @property
    def error_rate(self) -> float:
        if self.reference_length == 0:
            return 0.0
        return (self.substitutions + self.deletions + self.insertions) / self.reference_length

    @property
    def errors(self) -> list[tuple[str, str | None, str | None]]:
        return [e for e in self.edits if e[0] != "hit"]


def align(reference: list[str], hypothesis: list[str]) -> Alignment:
    """Levenshtein alignment with edit backtrace."""
    rows, cols = len(reference) + 1, len(hypothesis) + 1
    cost = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        cost[i][0] = i
    for j in range(cols):
        cost[0][j] = j

    for i in range(1, rows):
        for j in range(1, cols):
            if reference[i - 1] == hypothesis[j - 1]:
                cost[i][j] = cost[i - 1][j - 1]
            else:
                cost[i][j] = 1 + min(cost[i - 1][j - 1], cost[i - 1][j], cost[i][j - 1])

    result = Alignment()
    i, j = len(reference), len(hypothesis)
    while i > 0 or j > 0:
        if i > 0 and j > 0 and reference[i - 1] == hypothesis[j - 1] and cost[i][j] == cost[i - 1][j - 1]:
            result.hits += 1
            result.edits.append(("hit", reference[i - 1], hypothesis[j - 1]))
            i, j = i - 1, j - 1
        elif i > 0 and j > 0 and cost[i][j] == cost[i - 1][j - 1] + 1:
            result.substitutions += 1
            result.edits.append(("sub", reference[i - 1], hypothesis[j - 1]))
            i, j = i - 1, j - 1
        elif i > 0 and cost[i][j] == cost[i - 1][j] + 1:
            result.deletions += 1
            result.edits.append(("del", reference[i - 1], None))
            i -= 1
        else:
            result.insertions += 1
            result.edits.append(("ins", None, hypothesis[j - 1]))
            j -= 1

    result.edits.reverse()
    return result


def tokenize(text: str, language: str | None = None) -> list[str]:
    """Normalise then split into comparable words."""
    return normalize.without_fillers(text).split()


def wer(reference: str, hypothesis: str, language: str | None = None) -> Alignment:
    return align(tokenize(reference, language), tokenize(hypothesis, language))


def cer(reference: str, hypothesis: str, language: str | None = None) -> Alignment:
    """Character error rate.

    The meaningful figure for Indic scripts, where word-segmentation conventions
    make word-level rates unstable across otherwise-identical transcripts.
    """
    ref = list(normalize.basic(reference).replace(" ", ""))
    hyp = list(normalize.basic(hypothesis).replace(" ", ""))
    return align(ref, hyp)


_ENTITY = re.compile(r"^(?:\d+|[A-Z][\w-]*)$")


def is_entity(token: str) -> bool:
    """Numbers, names, and identifiers — the tokens whose loss changes an outcome."""
    return bool(_ENTITY.match(token)) or token.isdigit()


def entity_error_rate(reference: str, hypothesis: str, entities: list[str] | None = None) -> Alignment:
    """Error rate restricted to tokens that carry meaning.

    When `entities` is given, only those values are scored — the phone number and
    medication name matter; "um" does not.
    """
    if entities:
        ref_tokens: list[str] = []
        for value in entities:
            ref_tokens.extend(tokenize(str(value)))
    else:
        ref_tokens = [t for t in re.findall(r"\S+", reference) if is_entity(t)]
        ref_tokens = [t for token in ref_tokens for t in tokenize(token)]

    if not ref_tokens:
        return Alignment()

    hyp_tokens = tokenize(hypothesis)
    # Score each reference entity token against the best window of the hypothesis
    # so that surrounding words neither help nor hurt.
    result = Alignment()
    for token in ref_tokens:
        if token in hyp_tokens:
            result.hits += 1
            result.edits.append(("hit", token, token))
        else:
            result.substitutions += 1
            closest = min(hyp_tokens, key=lambda h: align([token], [h]).error_rate, default=None)
            result.edits.append(("sub", token, closest))
    return result
