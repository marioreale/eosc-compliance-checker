"""LP-LANG-01 — landing page available in English.

Tier 3: the irreducibly fuzzy one. A page can carry lang="en" and still serve
its substantive content in another language, so this rule is built as a signal
pipeline that DEGRADES TO A HUMAN rather than guessing.

The design rule to internalise: a heuristic check must never emit a confident
FAIL. Its adverse outcome is MANUAL_REVIEW.
"""

from __future__ import annotations

from ..api import CheckContext, check
from ..models import Evidence, Finding, Severity, Verdict

MIN_TEXT_CHARS = 200
CONFIDENCE_FLOOR = 0.65
ENGLISH_SWITCHER_PATTERNS = [
    r"(?i)^\s*(EN|ENG|English)\s*$",
    r"(?i)\bin\s+English\b",
    r"(?i)\bEnglish\s+version\b",
]


def _detect_language(text: str) -> tuple[str | None, float]:
    """Statistical detection, if lingua is installed. Returns (iso639-1, confidence)."""
    try:
        from lingua import LanguageDetectorBuilder
    except ImportError:
        return None, 0.0
    detector = (
        LanguageDetectorBuilder.from_all_spoken_languages()
        .with_preloaded_language_models()
        .build()
    )
    values = detector.compute_language_confidence_values(text)
    if not values:
        return None, 0.0
    top = values[0]
    return top.language.iso_code_639_1.name.lower(), float(top.value)


@check(
    id="LP-LANG-01",
    version="0.9.0",
    title="Landing page content available in English",
    requirement_ref="Landing Page Requirements 2.3",
    severity=Severity.MANDATORY,
    applies_to=["node_landing_page"],
)
def english_available(ctx: CheckContext) -> list[Finding]:
    """English must be available to federation users from outside the host country."""
    page = ctx.entry
    if page is None or not page.ok:
        return [
            ctx.finding(
                Verdict.ERROR,
                message="Landing page could not be collected; cannot evaluate language.",
            )
        ]

    signals: dict[str, object] = {
        "lang_attr": page.lang_attr,
        "hreflang": page.hreflang,
        "text_chars": len(page.text),
    }

    lang_attr_en = bool(page.lang_attr and page.lang_attr.lower().startswith("en"))
    hreflang_en = any(h.lower().startswith("en") for h in page.hreflang)
    switcher = ctx.match_controls(page, ENGLISH_SWITCHER_PATTERNS)
    switcher_en = bool(switcher)
    signals["english_switcher"] = [s.all_text[:40] for s in switcher[:3]]

    detected, confidence = _detect_language(page.text[:4000])
    signals["detected_language"] = detected
    signals["detection_confidence"] = round(confidence, 3)
    detector_available = detected is not None

    def ev(extra_note: str | None = None) -> list[Evidence]:
        return [
            Evidence(
                url=page.final_url,
                locator="html[lang], link[hreflang], main text",
                matched_text=page.text[:300] or None,
                screenshot=page.screenshot,
                extra={**signals, **({"note": extra_note} if extra_note else {})},
            )
        ]

    # Not enough text to judge statistically -- do not pretend otherwise.
    if len(page.text) < MIN_TEXT_CHARS:
        return [
            ctx.finding(
                Verdict.MANUAL_REVIEW,
                message=(
                    f"Only {len(page.text)} characters of main content were extracted; "
                    "too little to determine the language reliably."
                ),
                confidence=0.0,
                evidence=ev("insufficient text for detection"),
            )
        ]

    if not detector_available:
        return [
            ctx.finding(
                Verdict.MANUAL_REVIEW,
                message=(
                    "Language detector not installed (pip install 'compliance-checker[lang]'); "
                    f"declared lang attribute is {page.lang_attr!r}."
                ),
                confidence=0.0,
                evidence=ev("lingua not available"),
            )
        ]

    detected_en = detected == "en"
    confident = confidence >= CONFIDENCE_FLOOR

    # Detected English with confidence is the only case worth a firm PASS. The
    # declared attribute is recorded as a signal but is not required to agree:
    # plenty of genuinely English pages simply forget to set lang.
    if detected_en and confident:
        return [
            ctx.finding(
                Verdict.PASS,
                message=f"Main content detected as English (confidence {confidence:.2f}).",
                confidence=confidence,
                evidence=ev(),
            )
        ]

    # The classic trap: declared English, content demonstrably not English.
    # Note that only `lang` on this page counts as a claim about THIS page --
    # an hreflang="en" alternate merely advertises that another URL is English,
    # so it must not be read as a mismatch.
    if lang_attr_en and not detected_en and confident:
        return [
            ctx.finding(
                Verdict.MANUAL_REVIEW,
                message=(
                    f"Page declares {page.lang_attr or 'en'!r} but main content was detected "
                    f"as {detected!r} (confidence {confidence:.2f}). Declared and actual "
                    "language disagree; needs a human decision."
                ),
                confidence=confidence,
                remediation=(
                    "Either provide genuine English content or correct the declared "
                    "language attribute."
                ),
                evidence=ev("declared/detected mismatch"),
            )
        ]

    # Non-English content, but an English route appears to exist.
    if not detected_en and (hreflang_en or switcher_en):
        return [
            ctx.finding(
                Verdict.MANUAL_REVIEW,
                message=(
                    f"Landing page content detected as {detected!r}, but an English "
                    "alternative appears to be offered. Verify the English route reaches "
                    "equivalent content."
                ),
                confidence=confidence,
                evidence=ev("english alternative advertised"),
            )
        ]

    # Non-English, no English route found. Still not a confident FAIL: this rule
    # is heuristic by nature and a wrong FAIL costs more than a manual review.
    return [
        ctx.finding(
            Verdict.MANUAL_REVIEW,
            message=(
                f"No English version identified; main content detected as {detected!r} "
                f"(confidence {confidence:.2f}) with no English alternate or switcher."
            ),
            confidence=confidence,
            remediation=(
                "Provide an English version of the landing page and advertise it with "
                'a <link rel="alternate" hreflang="en"> or a visible language switcher.'
            ),
            evidence=ev("no english route found"),
        )
    ]
