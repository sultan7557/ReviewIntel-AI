import json
import logging
import os
from typing import TypeVar

from groq import AsyncGroq
from groq import BadRequestError, RateLimitError
from pydantic import BaseModel

from config import GROQ_MODEL
from models import ClaimExtraction, GapAnalysis, SentimentAnalysis
from prompts import (
    CLAIMS_PROMPT_V1,
    GAP_PROMPT_V1,
    REPORT_PROMPT_V1,
    SENTIMENT_PROMPT_V1,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def _get_client() -> AsyncGroq:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not set. Add it to your .env file.")
    return AsyncGroq(api_key=api_key)


def _friendly_error(error: Exception) -> str:
    msg = str(error)
    if "rate" in msg.lower() or "limit" in msg.lower():
        return (
            "Groq rate limit hit. Wait a minute and retry, or set GROQ_MODEL=llama-3.1-8b-instant "
            "in .env for a higher free-tier limit.\n\n"
            f"Original error: {msg}"
        )
    return f"Groq request failed: {msg}"


async def _structured_call(
    output_model: type[T],
    system: str,
    user_content: str,
    max_tokens: int = 4096,
) -> T:
    client = _get_client()
    schema_hint = json.dumps(output_model.model_json_schema(), indent=2)
    system_prompt = (
        f"{system}\n\n"
        "Respond with valid JSON only — no markdown, no code fences. "
        f"Match this schema exactly:\n{schema_hint}"
    )

    try:
        response = await client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=max_tokens,
        )
        content = response.choices[0].message.content
        if not content:
            raise ValueError("Empty response from Groq")
        return output_model.model_validate_json(content)
    except RateLimitError as e:
        logger.error("Groq rate limit: %s", e)
        raise RuntimeError(_friendly_error(e)) from e
    except BadRequestError as e:
        logger.error("Groq bad request: %s", e)
        raise RuntimeError(_friendly_error(e)) from e
    except Exception as e:
        logger.error("Groq error: %s", e)
        raise RuntimeError(_friendly_error(e)) from e


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
    client = _get_client()
    context = (
        f"Product Name: {product_name}\n\n"
        f"## Sentiment Analysis\n{sentiment.model_dump_json(indent=2)}\n\n"
        f"## Claims Extraction\n{claims.model_dump_json(indent=2)}\n\n"
        f"## Gap Analysis\n{gaps.model_dump_json(indent=2)}"
    )

    try:
        response = await client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": REPORT_PROMPT_V1},
                {
                    "role": "user",
                    "content": f"Generate the intelligence report for this product:\n\n{context}",
                },
            ],
            temperature=0.4,
            max_tokens=8192,
        )
        content = response.choices[0].message.content
        if not content:
            raise ValueError("No text content in report generation response")
        return content
    except RateLimitError as e:
        logger.error("Groq rate limit in report generation: %s", e)
        raise RuntimeError(_friendly_error(e)) from e
    except Exception as e:
        logger.error("Groq error in report generation: %s", e)
        raise RuntimeError(_friendly_error(e)) from e
