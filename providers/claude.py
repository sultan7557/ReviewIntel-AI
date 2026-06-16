import json
import logging
from typing import Any, TypeVar

import anthropic
from pydantic import BaseModel

from config import CLAUDE_MODEL
from models import ClaimExtraction, GapAnalysis, SentimentAnalysis
from prompts import (
    CLAIMS_PROMPT_V1,
    GAP_PROMPT_V1,
    REPORT_PROMPT_V1,
    SENTIMENT_PROMPT_V1,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


def _get_client() -> anthropic.AsyncAnthropic:
    import os

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set. Add it to your .env file.")
    return anthropic.AsyncAnthropic(api_key=api_key)


def _tool_schema(name: str, description: str, model: type[BaseModel]) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "input_schema": model.model_json_schema(),
    }


def _extract_tool_input(response: anthropic.types.Message, tool_name: str) -> dict[str, Any]:
    for block in response.content:
        if block.type == "tool_use" and block.name == tool_name:
            return block.input
    raise ValueError(f"No tool_use block found for tool '{tool_name}'")


async def _structured_call(
    tool_name: str,
    tool_description: str,
    output_model: type[T],
    system: str,
    user_content: str,
    max_tokens: int = 4096,
) -> T:
    client = _get_client()
    try:
        response = await client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=max_tokens,
            system=system,
            tools=[_tool_schema(tool_name, tool_description, output_model)],
            tool_choice={"type": "tool", "name": tool_name},
            messages=[{"role": "user", "content": user_content}],
        )
        data = _extract_tool_input(response, tool_name)
        return output_model.model_validate(data)
    except anthropic.APIError as e:
        logger.error("Anthropic API error: %s", e)
        msg = e.message or str(e)
        if "credit balance" in msg.lower() or "billing" in msg.lower():
            raise RuntimeError(
                "Anthropic credits exhausted. Add credits at console.anthropic.com "
                "or switch to Groq: set AI_PROVIDER=groq and GROQ_API_KEY in .env"
            ) from e
        raise RuntimeError(f"Claude request failed: {msg}") from e


async def run_sentiment_analysis(reviews: str) -> SentimentAnalysis:
    return await _structured_call(
        "submit_sentiment_analysis",
        "Submit structured sentiment analysis of product reviews",
        SentimentAnalysis,
        SENTIMENT_PROMPT_V1,
        f"Analyze the following Amazon product reviews:\n\n{reviews}",
    )


async def run_claims_extraction(reviews: str) -> ClaimExtraction:
    return await _structured_call(
        "submit_claims_extraction",
        "Submit structured claims extraction from product reviews",
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
    return await _structured_call(
        "submit_gap_analysis",
        "Submit structured competitor gap analysis",
        GapAnalysis,
        GAP_PROMPT_V1,
        user_content,
    )


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
        response = await client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=8192,
            system=REPORT_PROMPT_V1,
            messages=[
                {
                    "role": "user",
                    "content": f"Generate the intelligence report for this product:\n\n{context}",
                }
            ],
        )
        text_blocks = [block.text for block in response.content if block.type == "text"]
        if not text_blocks:
            raise ValueError("No text content in report generation response")
        return "\n".join(text_blocks)
    except anthropic.APIError as e:
        logger.error("Anthropic API error in report generation: %s", e)
        msg = e.message or str(e)
        if "credit balance" in msg.lower() or "billing" in msg.lower():
            raise RuntimeError(
                "Anthropic credits exhausted. Add credits at console.anthropic.com "
                "or switch to Groq: set AI_PROVIDER=groq and GROQ_API_KEY in .env"
            ) from e
        raise RuntimeError(f"Report generation failed: {msg}") from e
