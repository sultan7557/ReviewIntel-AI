from config import get_provider
from models import ClaimExtraction, GapAnalysis, SentimentAnalysis
from providers import claude, gemini, groq


def _active_provider():
    provider = get_provider()
    if provider == "gemini":
        return gemini
    if provider == "groq":
        return groq
    return claude


async def run_sentiment_analysis(reviews: str) -> SentimentAnalysis:
    return await _active_provider().run_sentiment_analysis(reviews)


async def run_claims_extraction(reviews: str) -> ClaimExtraction:
    return await _active_provider().run_claims_extraction(reviews)


async def run_gap_analysis(sentiment_themes: list[dict], competitor_reviews: str) -> GapAnalysis:
    return await _active_provider().run_gap_analysis(sentiment_themes, competitor_reviews)


async def run_report_generation(
    product_name: str,
    sentiment: SentimentAnalysis,
    claims: ClaimExtraction,
    gaps: GapAnalysis,
) -> str:
    return await _active_provider().run_report_generation(product_name, sentiment, claims, gaps)
