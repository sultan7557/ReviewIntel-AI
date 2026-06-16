import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from config import GEMINI_STEP_DELAY, PIPELINE_API_CALLS, get_provider, validate_provider_credentials
from models import AnalyzeRequest
from pipeline import (
    run_claims_extraction,
    run_gap_analysis,
    run_report_generation,
    run_sentiment_analysis,
)

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="Amazon Review Intelligence")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _pace_steps():
    """Space out Gemini calls to avoid per-minute rate limits on free tier."""
    if get_provider() == "gemini" and GEMINI_STEP_DELAY > 0:
        logger.info("Pacing %.1fs before next Gemini call (free-tier rate limit)", GEMINI_STEP_DELAY)
        await asyncio.sleep(GEMINI_STEP_DELAY)


@app.on_event("startup")
async def startup():
    provider = get_provider()
    validate_provider_credentials()
    logger.info("AI provider: %s | %d API calls per analysis run", provider, PIPELINE_API_CALLS)


@app.get("/")
async def serve_index():
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/analyze")
async def analyze(request: AnalyzeRequest):
    async def event_stream():
        validate_provider_credentials()
        logger.info(
            "Pipeline starting — %d sequential API calls (1 per step, no parallel requests)",
            PIPELINE_API_CALLS,
        )
        sentiment = None
        claims = None
        gaps = None

        # Step 1: Sentiment Analysis
        step = 1
        yield _sse_event("step_start", {"step": step, "name": "Sentiment & Theme Analysis"})
        t0 = time.perf_counter()
        logger.info("Step %d started: Sentiment & Theme Analysis", step)
        try:
            sentiment = await run_sentiment_analysis(request.product_reviews)
            elapsed = time.perf_counter() - t0
            logger.info("Step %d complete in %.2fs", step, elapsed)
            yield _sse_event("step_complete", {"step": step, "result": sentiment.model_dump()})
        except Exception as e:
            elapsed = time.perf_counter() - t0
            logger.error("Step %d failed after %.2fs: %s", step, elapsed, e)
            yield _sse_event("error", {"step": step, "message": str(e)})

        # Step 2: Claims Extraction (independent of step 1)
        await _pace_steps()
        step = 2
        yield _sse_event("step_start", {"step": step, "name": "Ingredient & Claims Extraction"})
        t0 = time.perf_counter()
        logger.info("Step %d started: Ingredient & Claims Extraction", step)
        try:
            claims = await run_claims_extraction(request.product_reviews)
            elapsed = time.perf_counter() - t0
            logger.info("Step %d complete in %.2fs", step, elapsed)
            yield _sse_event("step_complete", {"step": step, "result": claims.model_dump()})
        except Exception as e:
            elapsed = time.perf_counter() - t0
            logger.error("Step %d failed after %.2fs: %s", step, elapsed, e)
            yield _sse_event("error", {"step": step, "message": str(e)})

        # Step 3: Gap Analysis (needs step 1)
        await _pace_steps()
        step = 3
        yield _sse_event("step_start", {"step": step, "name": "Competitor Gap Analysis"})
        t0 = time.perf_counter()
        logger.info("Step %d started: Competitor Gap Analysis", step)
        if sentiment is None:
            msg = "Skipped: sentiment analysis did not complete"
            logger.warning("Step %d skipped: %s", step, msg)
            yield _sse_event("error", {"step": step, "message": msg})
        else:
            try:
                themes = [t.model_dump() for t in sentiment.themes]
                gaps = await run_gap_analysis(themes, request.competitor_reviews)
                elapsed = time.perf_counter() - t0
                logger.info("Step %d complete in %.2fs", step, elapsed)
                yield _sse_event("step_complete", {"step": step, "result": gaps.model_dump()})
            except Exception as e:
                elapsed = time.perf_counter() - t0
                logger.error("Step %d failed after %.2fs: %s", step, elapsed, e)
                yield _sse_event("error", {"step": step, "message": str(e)})

        # Step 4: Report Generation (needs all three)
        await _pace_steps()
        step = 4
        yield _sse_event("step_start", {"step": step, "name": "Generating Intelligence Report"})
        t0 = time.perf_counter()
        logger.info("Step %d started: Generating Intelligence Report", step)
        if sentiment is None or claims is None or gaps is None:
            missing = []
            if sentiment is None:
                missing.append("sentiment analysis")
            if claims is None:
                missing.append("claims extraction")
            if gaps is None:
                missing.append("gap analysis")
            msg = f"Skipped: missing {', '.join(missing)}"
            logger.warning("Step %d skipped: %s", step, msg)
            yield _sse_event("error", {"step": step, "message": msg})
        else:
            try:
                report = await run_report_generation(
                    request.product_name, sentiment, claims, gaps
                )
                elapsed = time.perf_counter() - t0
                logger.info("Step %d complete in %.2fs", step, elapsed)
                yield _sse_event(
                    "step_complete", {"step": step, "result": {"report_markdown": report}}
                )
            except Exception as e:
                elapsed = time.perf_counter() - t0
                logger.error("Step %d failed after %.2fs: %s", step, elapsed, e)
                yield _sse_event("error", {"step": step, "message": str(e)})

        yield _sse_event(
            "complete",
            {
                "product_name": request.product_name,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )

    return StreamingResponse(event_stream(), media_type="text/event-stream")


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
