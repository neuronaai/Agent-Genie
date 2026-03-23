"""Pydantic schemas for the OpenAI Brain microservice.

These define the expanded agent draft structure required by the prompt.
"""
from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
class HealthResponse(BaseModel):
    status: str
    mock_mode: bool
    openai_configured: bool


# ---------------------------------------------------------------------------
# Agent Draft Generation
# ---------------------------------------------------------------------------
class AgentDraftRequest(BaseModel):
    raw_prompt: str = Field(..., description="Natural-language description of the agent")
    tenant_id: Optional[str] = None
    language: str = "en-US"


class HandoffRuleOut(BaseModel):
    condition: str
    destination_number: Optional[str] = None
    transfer_message: Optional[str] = None


class GuardrailRuleOut(BaseModel):
    prohibited_topic: str
    fallback_message: str = "I cannot discuss that topic."


class FAQItem(BaseModel):
    question: str
    answer: str


class ServiceItem(BaseModel):
    name: str
    description: Optional[str] = None


class HoursOfOperation(BaseModel):
    timezone: str = "UTC"
    schedule: dict = Field(default_factory=dict, description="Day-of-week to open/close times")


class AgentDraftConfig(BaseModel):
    """Expanded agent config matching the prompt-required fields."""
    business_type: Optional[str] = None
    business_context: Optional[str] = None
    agent_role: Optional[str] = None
    agent_name: Optional[str] = None
    tone: str = "professional"
    language: str = "en-US"
    greeting_message: Optional[str] = None
    services: list[ServiceItem] = Field(default_factory=list)
    faqs: list[FAQItem] = Field(default_factory=list)
    knowledge_categories: list[str] = Field(default_factory=list)
    specials_offers: list[str] = Field(default_factory=list)
    human_handoff_conditions: list[HandoffRuleOut] = Field(default_factory=list)
    booking_behavior: Optional[str] = None
    support_flow: Optional[str] = None
    transfer_rules: list[HandoffRuleOut] = Field(default_factory=list)
    fallback_behavior: Optional[str] = None
    prohibited_topics: list[GuardrailRuleOut] = Field(default_factory=list)
    escalation_rules: list[str] = Field(default_factory=list)
    unsupported_request_behavior: Optional[str] = None
    hours_of_operation: Optional[HoursOfOperation] = None
    routing_rules: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)


class AgentDraftResponse(BaseModel):
    status: str  # "success" | "error"
    config: Optional[AgentDraftConfig] = None
    message: Optional[str] = None


# ---------------------------------------------------------------------------
# Knowledge Base Structuring
# ---------------------------------------------------------------------------
class KBStructureRequest(BaseModel):
    raw_content: str
    content_type: str = "text"  # text, faq, url, file_text
    agent_context: Optional[str] = None


class KBItem(BaseModel):
    category: str
    title: str
    content: str
    type: str = "text"


class KBStructureResponse(BaseModel):
    status: str
    items: list[KBItem] = Field(default_factory=list)
    message: Optional[str] = None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
class ValidationRequest(BaseModel):
    config: dict


class ValidationIssue(BaseModel):
    field: str
    severity: str  # "error" | "warning" | "info"
    message: str


class ValidationResponse(BaseModel):
    status: str  # "valid" | "invalid" | "warnings"
    issues: list[ValidationIssue] = Field(default_factory=list)
    message: Optional[str] = None
