from __future__ import annotations

from recut.utils import parse_float_env


class Thresholds:
    LOW: float = parse_float_env("RECUT_FLAG_THRESHOLD_LOW", 0.4)
    MEDIUM: float = parse_float_env("RECUT_FLAG_THRESHOLD_MEDIUM", 0.65)
    HIGH: float = parse_float_env("RECUT_FLAG_THRESHOLD_HIGH", 0.85)


UNCERTAINTY_PHRASES = [
    "not sure",
    "uncertain",
    "might",
    "could be wrong",
    "i'm not confident",
    "i am not confident",
    "unclear",
    "possibly",
    "i think",
    "i believe",
    "not certain",
    "may be incorrect",
    "i'm unsure",
    "i am unsure",
    "not entirely sure",
]

CONFIDENCE_PHRASES = [
    "definitely",
    "certainly",
    "the answer is",
    "i know that",
    "clearly",
    "obviously",
    "without a doubt",
    "i am confident",
    "i'm confident",
    "absolutely",
    "undoubtedly",
    "it is clear",
]
