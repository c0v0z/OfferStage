import json
import re
import uuid
from typing import Any

from .database import Database
from .llm import LLMClient
from .retrieval import RetrievalService
from .schemas import (
    Analysis,
    AnswerResponse,
    ApplicationRecord,
    InterviewQuestion,
    InterviewStartResponse,
    JobProfile,
    Recommendation,
    ResumeProfile,
    RewriteResponse,
    RetrievedExperience,
)


def dump_model(model: Any) -> dict[str, Any]:
    return model.model_dump() if hasattr(model, "model_dump") else model.dict()


def _normalise(value: str) -> str:
    return re.sub(r"\s+", "", value or "").lower()


def _evidence_grounded(text: str, source: str, resume_text: str, job_text: str) -> bool:
    if source == "user":
        return True
    haystack = _normalise(resume_text if source == "resume" else job_text)
    needle = _normalise(text)
    return bool(needle and len(needle) >= 2 and needle in haystack)


class OfferSageAgent:
    def __init__(self, database: Database, llm: LLMClient, retrieval: RetrievalService | None = None):
        self.database = database
        self.llm = llm
        self.retrieval = retrieval or RetrievalService(database, llm.settings)

    def _ground_analysis(self, analysis: Analysis, resume_text: str, job_text: str) -> Analysis:
        for collection in (analysis.matched_requirements, analysis.missing_requirements, analysis.strengths, analysis.gaps, analysis.recommendations):
            for item in collection:
                grounded = []
                for evidence in item.evidence:
                    if _evidence_grounded(evidence.text, evidence.source, resume_text, job_text):
                        grounded.append(evidence)
                item.evidence = grounded
                if isinstance(item, Recommendation) and not grounded:
                    item.needs_confirmation = True
        return analysis

    async def analyze(self, resume_text: str, job_text: str, profile_id: str | None = None, profile_name: str = "") -> ApplicationRecord:
        resume = await self.llm.extract_resume(resume_text)
        job = await self.llm.extract_job(job_text)
        profile_id = await self.retrieval.ensure_profile(profile_id, resume.name or profile_name or "我的求职档案")
        await self.retrieval.index_resume(profile_id, resume_text, resume)
        query = "\n".join([job.title, job.summary, *job.must_have, *job.responsibilities, *job.keywords])
        retrieved = await self.retrieval.search(profile_id, query, limit=8)
        analysis = await self.llm.match(resume, job, retrieved)
        analysis = self._ground_analysis(analysis, resume_text, job_text)
        application_id = str(uuid.uuid4())
        payload = {"resume": dump_model(resume), "job": dump_model(job), "analysis": dump_model(analysis)}
        created_at = self.database.save_application(application_id, resume_text, job_text, payload, profile_id, [dump_model(item) for item in retrieved])
        from datetime import datetime

        return ApplicationRecord(id=application_id, created_at=datetime.fromisoformat(created_at), resume=resume, job=job, analysis=analysis, profile_id=profile_id, retrieved_experiences=retrieved)

    def _load_application(self, application_id: str) -> tuple[ResumeProfile, JobProfile, Analysis, str, str]:
        row = self.database.get_application(application_id)
        if not row:
            raise KeyError("找不到对应的分析记录。")
        return (
            ResumeProfile.model_validate(json.loads(row["resume_json"])),
            JobProfile.model_validate(json.loads(row["job_json"])),
            Analysis.model_validate(json.loads(row["analysis_json"])),
            row["resume_text"],
            row["jd_text"],
        )

    def _load_retrieved(self, application_id: str, profile_id: str) -> list[RetrievedExperience]:
        row = self.database.get_application(application_id)
        if not row:
            return []
        try:
            return [RetrievedExperience.model_validate(item) for item in json.loads(row["retrieved_json"] or "[]")]
        except (json.JSONDecodeError, TypeError):
            return []

    async def rewrite(self, application_id: str) -> RewriteResponse:
        resume, job, analysis, resume_text, _ = self._load_application(application_id)
        row = self.database.get_application(application_id)
        retrieved = self._load_retrieved(application_id, row["profile_id"] if row else "")
        if not retrieved and row and row["profile_id"]:
            retrieved = await self.retrieval.search(row["profile_id"], job.title + " " + job.summary, limit=8)
        language = "zh" if len(re.findall(r"[\u4e00-\u9fff]", job.summary)) >= len(re.findall(r"[A-Za-z]", job.summary)) else "en"
        result = await self.llm.rewrite(resume, job, analysis, language, retrieved)
        result.application_id = application_id
        grounded_bullets = []
        for item in result.bullets:
            grounded = [evidence for evidence in item.evidence if _evidence_grounded(evidence.text, evidence.source, resume_text, "")]
            item.evidence = grounded
            if not grounded:
                item.needs_confirmation = True
            else:
                grounded_bullets.append(item)
        result.bullets = grounded_bullets
        return result

    def profiles(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.retrieval.list_profiles(limit)

    async def create_profile(self, name: str) -> dict[str, Any]:
        profile_id = await self.retrieval.ensure_profile(None, name)
        row = self.database.get_profile(profile_id)
        return dict(row) if row else {"id": profile_id, "name": name}

    async def add_experience(self, profile_id: str, payload: Any) -> Any:
        return await self.retrieval.add_experience(profile_id, payload.kind, payload.title, payload.text, payload.organization, payload.period)

    def experiences(self, profile_id: str) -> list[Any]:
        if not self.database.get_profile(profile_id):
            raise KeyError("找不到对应的候选人档案。")
        return self.retrieval.list_experiences(profile_id)

    async def retrieve(self, profile_id: str, query: str, limit: int = 8) -> list[RetrievedExperience]:
        if not self.database.get_profile(profile_id):
            raise KeyError("找不到对应的候选人档案。")
        return await self.retrieval.search(profile_id, query, limit)

    async def start_interview(self, application_id: str, count: int = 5) -> InterviewStartResponse:
        _, job, analysis, _, _ = self._load_application(application_id)
        questions = await self.llm.interview_questions(job, analysis, max(1, min(count, 10)))
        if not questions:
            raise ValueError("当前职位没有可生成的面试问题。")
        session_id = str(uuid.uuid4())
        question_payload = [dump_model(question) for question in questions]
        self.database.save_interview(session_id, application_id, question_payload)
        return InterviewStartResponse(session_id=session_id, application_id=application_id, questions=questions, current_question=questions[0])

    async def answer_interview(self, session_id: str, question_id: str, answer: str) -> AnswerResponse:
        if not answer.strip():
            raise ValueError("回答不能为空。")
        row = self.database.get_interview(session_id)
        if not row:
            raise KeyError("找不到对应的面试会话。")
        questions = [InterviewQuestion.model_validate(item) for item in json.loads(row["questions_json"])]
        current_index = int(row["current_index"])
        if current_index >= len(questions):
            raise ValueError("该面试已经完成。")
        current = questions[current_index]
        if current.id != question_id:
            raise ValueError("提交的问题不是当前问题，请刷新后重试。")
        next_question = questions[current_index + 1] if current_index + 1 < len(questions) else None
        result = await self.llm.evaluate_answer(answer, current, next_question)
        result.session_id = session_id
        result.question_id = current.id
        result.next_question = next_question
        result.completed = next_question is None
        transcript = json.loads(row["transcript_json"])
        transcript.append({"question_id": current.id, "question": current.question, "answer": answer, "score": result.score, "feedback": {"strengths": result.strengths, "improvements": result.improvements}})
        self.database.update_interview(session_id, transcript, current_index + 1, result.completed)
        return result

    def history(self, limit: int = 20) -> list[dict[str, Any]]:
        records = []
        for row in self.database.list_applications(limit):
            analysis = json.loads(row["analysis_json"])
            job = json.loads(row["job_json"])
            records.append({"id": row["id"], "created_at": row["created_at"], "title": job.get("title", "目标职位"), "score": analysis.get("overall_score", 0), "conclusion": analysis.get("conclusion", "")})
        return records
