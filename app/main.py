from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import OfferSageAgent
from .config import Settings, settings
from .database import Database
from .llm import LLMClient
from .parsers import extract_text
from .schemas import (
    AnswerResponse,
    ApplicationRecord,
    ErrorResponse,
    ExperienceCreateRequest,
    ExperienceRecord,
    HealthResponse,
    InterviewStartResponse,
    ProfileCreateRequest,
    ProfileSummary,
    RetrievalResponse,
    RewriteResponse,
    RetrievedExperience,
)


class RewriteRequest(BaseModel):
    application_id: str


class InterviewStartRequest(BaseModel):
    application_id: str
    count: int = Field(default=5, ge=1, le=10)


class InterviewAnswerRequest(BaseModel):
    question_id: str
    answer: str = Field(min_length=1, max_length=10000)


def create_app(settings_override: Settings | None = None, database: Database | None = None) -> FastAPI:
    app_settings = settings_override or settings
    db = database or Database(app_settings.database_path)
    agent = OfferSageAgent(db, LLMClient(app_settings))

    application = FastAPI(title="OfferSage 求职助手", version="0.1.0", docs_url="/docs")
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost", "http://127.0.0.1", "http://localhost:8000", "http://127.0.0.1:8000"],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    static_dir = Path(__file__).resolve().parent.parent / "static"
    application.mount("/static", StaticFiles(directory=static_dir), name="static")

    @application.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @application.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", mock_llm=app_settings.mock_llm, model=app_settings.llm_model)

    @application.post("/api/analyze", response_model=ApplicationRecord, responses={400: {"model": ErrorResponse}})
    async def analyze(
        resume_text: str = Form(default=""),
        jd_text: str = Form(default=""),
        profile_id: str = Form(default=""),
        profile_name: str = Form(default=""),
        resume_file: UploadFile | None = File(default=None),
        jd_file: UploadFile | None = File(default=None),
    ) -> ApplicationRecord:
        try:
            if resume_file and resume_file.filename:
                resume_text = extract_text(await resume_file.read(), resume_file.filename)
            if jd_file and jd_file.filename:
                jd_text = extract_text(await jd_file.read(), jd_file.filename)
            resume_text = resume_text.strip()
            jd_text = jd_text.strip()
            if not resume_text or not jd_text:
                raise ValueError("请同时提供简历和职位描述，可以粘贴文本或上传文件。")
            if len(resume_text) > 50000 or len(jd_text) > 50000:
                raise ValueError("文本内容不能超过 50000 个字符。")
            return await agent.analyze(resume_text, jd_text, profile_id=profile_id or None, profile_name=profile_name)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"分析失败：{exc}") from exc

    @application.get("/api/applications/{application_id}", response_model=ApplicationRecord, responses={404: {"model": ErrorResponse}})
    async def get_application(application_id: str) -> ApplicationRecord:
        try:
            resume, job, analysis, _, _ = agent._load_application(application_id)
            row = db.get_application(application_id)
            from datetime import datetime

            retrieved = agent._load_retrieved(application_id, row["profile_id"] if row else "")
            return ApplicationRecord(id=application_id, created_at=datetime.fromisoformat(row["created_at"]), resume=resume, job=job, analysis=analysis, profile_id=row["profile_id"] if row else "", retrieved_experiences=retrieved)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @application.post("/api/rewrite", response_model=RewriteResponse, responses={404: {"model": ErrorResponse}})
    async def rewrite(request: RewriteRequest) -> RewriteResponse:
        try:
            return await agent.rewrite(request.application_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @application.post("/api/interview/start", response_model=InterviewStartResponse, responses={404: {"model": ErrorResponse}})
    async def start_interview(request: InterviewStartRequest) -> InterviewStartResponse:
        try:
            return await agent.start_interview(request.application_id, request.count)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @application.post("/api/interview/{session_id}/answer", response_model=AnswerResponse, responses={404: {"model": ErrorResponse}})
    async def answer_interview(session_id: str, request: InterviewAnswerRequest) -> AnswerResponse:
        try:
            return await agent.answer_interview(session_id, request.question_id, request.answer)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @application.get("/api/history")
    async def history(limit: int = 20) -> list[dict]:
        return agent.history(limit)

    @application.get("/api/profiles", response_model=list[ProfileSummary])
    async def profiles(limit: int = 50) -> list[ProfileSummary]:
        return [ProfileSummary.model_validate(item) for item in agent.profiles(limit)]

    @application.post("/api/profiles", response_model=ProfileSummary, responses={400: {"model": ErrorResponse}})
    async def create_profile(request: ProfileCreateRequest) -> ProfileSummary:
        try:
            profile = await agent.create_profile(request.name)
            return ProfileSummary.model_validate(profile)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @application.get("/api/profiles/{profile_id}/experiences", response_model=list[ExperienceRecord], responses={404: {"model": ErrorResponse}})
    async def experiences(profile_id: str) -> list[ExperienceRecord]:
        try:
            return agent.experiences(profile_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @application.post("/api/profiles/{profile_id}/experiences", response_model=ExperienceRecord, responses={404: {"model": ErrorResponse}})
    async def add_experience(profile_id: str, request: ExperienceCreateRequest) -> ExperienceRecord:
        try:
            return await agent.add_experience(profile_id, request)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @application.get("/api/profiles/{profile_id}/retrieve", response_model=RetrievalResponse, responses={404: {"model": ErrorResponse}})
    async def retrieve(profile_id: str, query: str, limit: int = 8) -> RetrievalResponse:
        try:
            results = await agent.retrieve(profile_id, query, limit)
            return RetrievalResponse(profile_id=profile_id, query=query, results=results)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
