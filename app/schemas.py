from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, ConfigDict


class Experience(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str = ""
    organization: str = ""
    period: str = ""
    bullets: list[str] = Field(default_factory=list)


class Education(BaseModel):
    school: str = ""
    degree: str = ""
    field: str = ""
    period: str = ""


class Project(BaseModel):
    name: str = ""
    description: str = ""
    technologies: list[str] = Field(default_factory=list)
    bullets: list[str] = Field(default_factory=list)


class ResumeProfile(BaseModel):
    name: str = ""
    summary: str = ""
    skills: list[str] = Field(default_factory=list)
    experiences: list[Experience] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)


class RetrievedExperience(BaseModel):
    id: str
    profile_id: str
    kind: Literal["project", "experience", "education", "skill", "resume"]
    title: str
    text: str
    score: float = Field(ge=0, le=1)
    source: Literal["profile", "resume"] = "profile"


class ProfileSummary(BaseModel):
    id: str
    name: str
    created_at: datetime
    experience_count: int = 0


class ExperienceRecord(BaseModel):
    id: str
    profile_id: str
    kind: Literal["project", "experience", "education", "skill", "resume"]
    title: str
    organization: str = ""
    period: str = ""
    text: str
    source: str = "manual"
    created_at: datetime


class ProfileCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ExperienceCreateRequest(BaseModel):
    kind: Literal["project", "experience", "education", "skill", "resume"] = "experience"
    title: str = Field(min_length=1, max_length=200)
    organization: str = ""
    period: str = ""
    text: str = Field(min_length=1, max_length=10000)


class RetrievalResponse(BaseModel):
    profile_id: str
    query: str
    results: list[RetrievedExperience] = Field(default_factory=list)


class JobProfile(BaseModel):
    title: str = ""
    company: str = ""
    summary: str = ""
    must_have: list[str] = Field(default_factory=list)
    nice_to_have: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)


class EvidenceItem(BaseModel):
    text: str
    source: Literal["resume", "job", "user"]


class MatchItem(BaseModel):
    requirement: str
    status: Literal["matched", "partial", "missing"]
    evidence: list[EvidenceItem] = Field(default_factory=list)
    note: str = ""


class Insight(BaseModel):
    title: str
    detail: str
    evidence: list[EvidenceItem] = Field(default_factory=list)


class Recommendation(BaseModel):
    priority: Literal["high", "medium", "low"]
    action: str
    rationale: str
    evidence: list[EvidenceItem] = Field(default_factory=list)
    needs_confirmation: bool = False


class Analysis(BaseModel):
    overall_score: int = Field(ge=0, le=100)
    conclusion: str
    matched_requirements: list[MatchItem] = Field(default_factory=list)
    missing_requirements: list[MatchItem] = Field(default_factory=list)
    strengths: list[Insight] = Field(default_factory=list)
    gaps: list[Insight] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    analysis_source: Literal["deepseek", "deepseek_repaired", "local_fallback"] = "deepseek"
    warning: str | None = None


class ApplicationRecord(BaseModel):
    id: str
    created_at: datetime
    resume: ResumeProfile
    job: JobProfile
    analysis: Analysis
    profile_id: str = ""
    retrieved_experiences: list[RetrievedExperience] = Field(default_factory=list)


class RewriteItem(BaseModel):
    original: str
    rewritten: str
    evidence: list[EvidenceItem] = Field(default_factory=list)
    needs_confirmation: bool = False


class RewriteResponse(BaseModel):
    application_id: str
    language: Literal["zh", "en"]
    summary: str
    bullets: list[RewriteItem] = Field(default_factory=list)


class InterviewQuestion(BaseModel):
    id: str
    question: str
    focus: str
    ideal_points: list[str] = Field(default_factory=list)


class InterviewStartResponse(BaseModel):
    session_id: str
    application_id: str
    questions: list[InterviewQuestion]
    current_question: InterviewQuestion | None = None


class AnswerResponse(BaseModel):
    session_id: str
    question_id: str
    score: int = Field(ge=0, le=10)
    strengths: list[str] = Field(default_factory=list)
    improvements: list[str] = Field(default_factory=list)
    follow_up: str = ""
    next_question: InterviewQuestion | None = None
    completed: bool = False


class HealthResponse(BaseModel):
    status: str
    mock_llm: bool
    model: str


class ErrorResponse(BaseModel):
    detail: str
