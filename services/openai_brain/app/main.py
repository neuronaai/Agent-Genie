"""OpenAI Brain Microservice — FastAPI entry point.

Provides AI-powered agent draft generation, knowledge-base structuring,
and validation endpoints.  Communicates with the main Flask app over HTTP.
"""
import os

from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware

from app.schemas import (
    AgentDraftRequest, AgentDraftResponse,
    KBStructureRequest, KBStructureResponse,
    ValidationRequest, ValidationResponse,
    HealthResponse,
)
from app.generator import generate_agent_draft, structure_knowledge_base, validate_agent_config

app = FastAPI(
    title="AgentGenie OpenAI Brain",
    version="1.0.0",
    description="Microservice for AI-powered agent configuration generation.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

INTERNAL_SERVICE_TOKEN = os.environ.get("BRAIN_SERVICE_TOKEN", "dev-token")


async def verify_service_token(x_service_token: str = Header(default="")):
    """Verify the internal service-to-service token."""
    if not INTERNAL_SERVICE_TOKEN:
        return  # No token configured — allow (dev mode)
    if x_service_token != INTERNAL_SERVICE_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid service token")


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/health", response_model=HealthResponse)
async def health():
    mock_mode = os.environ.get("BRAIN_MOCK_MODE", "false").lower() == "true"
    return HealthResponse(
        status="ok",
        mock_mode=mock_mode,
        openai_configured=bool(os.environ.get("OPENAI_API_KEY_CUSTOM") or os.environ.get("OPENAI_API_KEY")),
    )


# ---------------------------------------------------------------------------
# Agent Draft Generation
# ---------------------------------------------------------------------------
@app.post(
    "/v1/agent-drafts/generate",
    response_model=AgentDraftResponse,
    dependencies=[Depends(verify_service_token)],
)
async def generate_draft(req: AgentDraftRequest):
    """Generate a structured agent configuration from a natural-language prompt."""
    try:
        result = await generate_agent_draft(req)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Knowledge Base Structuring
# ---------------------------------------------------------------------------
@app.post(
    "/v1/knowledge-base/structure",
    response_model=KBStructureResponse,
    dependencies=[Depends(verify_service_token)],
)
async def structure_kb(req: KBStructureRequest):
    """Structure raw knowledge-base content into categorized items."""
    try:
        result = await structure_knowledge_base(req)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
@app.post(
    "/v1/agent-config/validate",
    response_model=ValidationResponse,
    dependencies=[Depends(verify_service_token)],
)
async def validate_config(req: ValidationRequest):
    """Validate an agent config for completeness and contradictions."""
    try:
        result = await validate_agent_config(req)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
