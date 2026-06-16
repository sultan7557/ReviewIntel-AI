from pydantic import BaseModel


class ReviewTheme(BaseModel):
    name: str
    sentiment_score: float  # -1.0 to 1.0
    review_count: int
    top_complaints: list[str]
    top_praises: list[str]
    sample_quote: str


class SentimentAnalysis(BaseModel):
    overall_score: float  # -1.0 to 1.0
    themes: list[ReviewTheme]
    summary: str


class ClaimExtraction(BaseModel):
    ingredient_mentions: list[str]
    benefit_claims: list[str]
    side_effect_mentions: list[str]
    unverified_claims: list[str]


class CompetitorGap(BaseModel):
    gap_description: str
    opportunity: str
    priority: str  # "high", "medium", "low"


class GapAnalysis(BaseModel):
    gaps: list[CompetitorGap]
    quick_wins: list[str]
    strategic_opportunities: list[str]


class PipelineResult(BaseModel):
    product_name: str
    sentiment: SentimentAnalysis
    claims: ClaimExtraction
    gaps: GapAnalysis
    report_markdown: str


class AnalyzeRequest(BaseModel):
    product_name: str
    product_reviews: str
    competitor_reviews: str
