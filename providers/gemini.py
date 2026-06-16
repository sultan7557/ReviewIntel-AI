import asyncio
import json
import logging
import os
import re
from typing import Callable, TypeVar

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel

from config import GEMINI_MAX_RETRIES, GEMINI_MAX_RETRY_DELAY, GEMINI_MODEL
from models import ClaimExtraction, GapAnalysis, SentimentAnalysis
from prompts import (
    CLAIMS_PROMPT_V1,
    GAP_PROMPT_V1,
    REPORT_PROMPT_V1,
    SENTIMENT_PROMPT_V1,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def _get_client() -> genai.Client:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set. Add it to your .env file.")
    return genai.Client(api_key=api_key)


def _parse_retry_delay(message: str) -> float:
    match = re.search(r"Please retry in ([\d.]+)s", message, re.IGNORECASE)
    if match:
        return float(match.group(1)) + 1.0
    return 5.0


def _is_rate_limited(error: genai_errors.APIError) -> bool:
    msg = (error.message or "").lower()
    return error.status == 429 or "quota" in msg or "rate" in msg or "resource_exhausted" in msg


def _friendly_quota_error(error: genai_errors.APIError) -> str:
    return (
        "Gemini API quota/rate limit hit. Fixes to try:\n"
        "1. Wait a few minutes (free tier resets per minute and daily)\n"
        "2. In .env set GEMINI_MODEL=gemini-2.0-flash-lite (separate quota pool)\n"
        "3. Enable billing at https://aistudio.google.com/apikey\n"
        "4. Create a new API key in a fresh Google Cloud project\n"
        "5. Switch to Claude: set AI_PROVIDER=claude and add ANTHROPIC_API_KEY\n\n"
        f"Original error: {error.message}"
    )


async def _with_retry(operation: str, call: Callable):
    last_error: genai_errors.APIError | None = None
    for attempt in range(1, GEMINI_MAX_RETRIES + 1):
        try:
            return await call()
        except genai_errors.APIError as e:
            last_error = e
            if _is_rate_limited(e) and attempt < GEMINI_MAX_RETRIES:
                delay = min(_parse_retry_delay(e.message or "") * attempt, GEMINI_MAX_RETRY_DELAY)
                logger.warning(
                    "%s rate limited — retry %d/%d in %.1fs",
                    operation,
                    attempt,
                    GEMINI_MAX_RETRIES,
                    delay,
                )
                await asyncio.sleep(delay)
                continue
            logger.error("Gemini API error in %s: %s", operation, e)
            if _is_rate_limited(e):
                raise RuntimeError(_friendly_quota_error(e)) from e
            raise RuntimeError(f"Gemini request failed: {e.message}") from e
    if last_error:
        if _is_rate_limited(last_error):
            raise RuntimeError(_friendly_quota_error(last_error)) from last_error
        raise RuntimeError(f"Gemini request failed: {last_error.message}") from last_error
    raise RuntimeError(f"Gemini request failed during {operation}")


async def _structured_call(
    output_model: type[T],
    system: str,
    user_content: str,
    max_tokens: int = 4096,
) -> T:
    async def _call():
        client = _get_client()
        response = await client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=user_content,
            config=types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
                response_schema=output_model,
                max_output_tokens=max_tokens,
                temperature=0.2,
            ),
        )
        if not response.text:
            raise ValueError("Empty response from Gemini")
        return output_model.model_validate_json(response.text)

    return await _with_retry("structured call", _call)


async def run_sentiment_analysis(reviews: str) -> SentimentAnalysis:
    return await _structured_call(
        SentimentAnalysis,
        SENTIMENT_PROMPT_V1,
        f"Analyze the following Amazon product reviews:\n\n{reviews}",
    )


async def run_claims_extraction(reviews: str) -> ClaimExtraction:
    return await _structured_call(
        ClaimExtraction,
        CLAIMS_PROMPT_V1,
        f"Extract claims and mentions from the following Amazon product reviews:\n\n{reviews}",
    )


async def run_gap_analysis(sentiment_themes: list[dict], competitor_reviews: str) -> GapAnalysis:
    themes_json = json.dumps(sentiment_themes, indent=2)
    user_content = (
        "Here are the themes from our product's customer reviews:\n\n"
        f"{themes_json}\n\n"
        "Here are the competitor's customer reviews:\n\n"
        f"{competitor_reviews}"
    )
    return await _structured_call(GapAnalysis, GAP_PROMPT_V1, user_content)


async def run_report_generation(
    product_name: str,
    sentiment: SentimentAnalysis,
    claims: ClaimExtraction,
    gaps: GapAnalysis,
) -> str:
    context = (
        f"Product Name: {product_name}\n\n"
        f"## Sentiment Analysis\n{sentiment.model_dump_json(indent=2)}\n\n"
        f"## Claims Extraction\n{claims.model_dump_json(indent=2)}\n\n"
        f"## Gap Analysis\n{gaps.model_dump_json(indent=2)}"
    )

    async def _call():
        client = _get_client()
        response = await client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=f"Generate the intelligence report for this product:\n\n{context}",
            config=types.GenerateContentConfig(
                system_instruction=REPORT_PROMPT_V1,
                max_output_tokens=8192,
                temperature=0.4,
            ),
        )
        if not response.text:
            raise ValueError("No text content in report generation response")
        return response.text

    return await _with_retry("report generation", _call)
