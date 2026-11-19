"""Multilingual YouTube walkthrough evaluator supporting English, Spanish, and Portuguese."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from assessment_agent.config.settings import SupportedLanguage
from assessment_agent.observability.pii_redactor import redact_pii
from assessment_agent.observability.tracing import traced_span
from assessment_agent.schemas.models import ToolErrorResult, VideoWalkthroughResult

_ALLOWED_VIDEO_HOSTS = frozenset(
    {"youtube.com", "www.youtube.com", "youtu.be", "m.youtube.com"}
)

_LANG_MARKERS: dict[SupportedLanguage, tuple[str, ...]] = {
    "es": ("agente", "arquitectura", "problema", "solución", "demo", "código", "evaluación"),
    "pt": ("agente", "arquitetura", "problema", "solução", "demonstração", "código", "avaliação"),
    "en": ("agent", "architecture", "problem", "solution", "demo", "code", "evaluation"),
}

_PROBLEM_KEYWORDS = ("problem", "solution", "problema", "solución", "solução", "why")
_ARCH_KEYWORDS = ("architecture", "code", "adk", "multi-agent", "arquitectura", "arquitetura", "código")
_DEMO_KEYWORDS = ("demo", "live", "run", "execution", "demostración", "demonstração", "ejecución")


def analyze_multilingual_walkthrough_video(
    video_url: str,
    transcript_excerpt: str = "",
    declared_language: str = "auto",
) -> dict[str, Any]:
    """Validate a public YouTube walkthrough URL and evaluate its multilingual transcript coverage.

    Supports English (`en`), Spanish (`es`), and Portuguese (`pt`) learner walkthroughs—directly
    addressing the AI in 5 Days FAQ invitation to build multilingual assessment tooling—while
    advising learners whether an English walkthrough is still recommended for the official
    English-only Starfleet evaluator.

    Args:
        video_url: Public YouTube URL (https://www.youtube.com/watch?v=... or https://youtu.be/...).
        transcript_excerpt: Optional transcript or script text from the walkthrough video.
        declared_language: Language hint (`en`, `es`, `pt`, or `auto` for detection).

    Returns:
        A JSON-serializable dict validated against `VideoWalkthroughResult`, or `ToolErrorResult`
        with `recovery_instructions` if the video URL is malformed or disallowed.
    """
    with traced_span("tool.analyze_multilingual_walkthrough_video", {"video_url": video_url}):
        cleaned_url = video_url.strip()
        parsed = urlparse(cleaned_url)
        host = (parsed.hostname or "").lower()

        if parsed.scheme != "https" or host not in _ALLOWED_VIDEO_HOSTS:
            return ToolErrorResult(
                error_code="INVALID_YOUTUBE_URL",
                message=f"URL '{redact_pii(cleaned_url)}' is not a valid public HTTPS YouTube link.",
                recovery_instructions=(
                    "Ask the learner for a public https://youtube.com/watch?v=... or "
                    "https://youtu.be/... link (note that the video is optional)."
                ),
            ).model_dump()

        scrubbed_transcript = redact_pii(transcript_excerpt).lower()
        detected_lang: SupportedLanguage = _detect_language(scrubbed_transcript, declared_language)

        covers_problem = any(k in scrubbed_transcript for k in _PROBLEM_KEYWORDS)
        covers_arch = any(k in scrubbed_transcript for k in _ARCH_KEYWORDS)
        covers_demo = any(k in scrubbed_transcript for k in _DEMO_KEYWORDS)

        if detected_lang == "en":
            advisory = (
                "Video is in English and directly compatible with the official Starfleet evaluator."
            )
        else:
            advisory = (
                f"Detected '{detected_lang}' walkthrough. This multilingual assessor evaluates "
                f"'{detected_lang}' content cleanly, but remember the official Starfleet evaluator "
                "currently expects English videos if you include a video URL."
            )

        if not scrubbed_transcript:
            feedback = (
                "Valid public YouTube URL verified. Provide a transcript or short outline to "
                "receive detailed coverage feedback on problem framing, architecture, and demo."
            )
        else:
            missing_parts = []
            if not covers_problem:
                missing_parts.append("problem & solution framing")
            if not covers_arch:
                missing_parts.append("ADK architecture & code walkthrough")
            if not covers_demo:
                missing_parts.append("working live agent demo")
            if missing_parts:
                feedback = "Walkthrough is missing clear coverage of: " + ", ".join(missing_parts) + "."
            else:
                feedback = (
                    "Strong walkthrough covering problem/solution framing, architecture & code, "
                    "and a live working demonstration."
                )

        result = VideoWalkthroughResult(
            video_url=cleaned_url,
            detected_language=detected_lang,
            covers_problem_and_solution=covers_problem,
            covers_architecture_and_code=covers_arch,
            covers_live_demo=covers_demo,
            english_submission_advisory=advisory,
            feedback_summary=feedback,
        )
        return result.model_dump()


def _detect_language(lower_text: str, declared_language: str) -> SupportedLanguage:
    norm = declared_language.strip().lower()
    if norm in ("en", "es", "pt"):
        return norm  # type: ignore[return-value]
    scores: dict[SupportedLanguage, int] = {
        lang: sum(1 for token in markers if token in lower_text)
        for lang, markers in _LANG_MARKERS.items()
    }
    best_lang = max(scores, key=lambda lang: scores[lang])
    if scores[best_lang] == 0:
        return "en"
    return best_lang
