import json
import logging
import re
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel

from .config import Settings
from .schemas import (
    Analysis,
    AnswerResponse,
    InterviewQuestion,
    JobProfile,
    ResumeProfile,
    RetrievedExperience,
    RewriteResponse,
)


ModelT = TypeVar("ModelT", bound=BaseModel)
logger = logging.getLogger("offersage.llm")


class StructuredOutputError(RuntimeError):
    """Safe public error for a provider response that never matched its contract."""

    def __init__(self, schema_name: str):
        super().__init__(f"{schema_name} 结构化输出无效")
        self.schema_name = schema_name


SYSTEM_PROMPT = """你是 OfferSage，一个严谨的求职助手。
你只能根据用户提供的简历、职位描述和回答工作，绝不编造经历、技能、公司或数字。
所有 evidence.text 必须是输入中的原文短句，并标明 source。若无法找到原文证据，needs_confirmation 必须为 true。
Analysis.overall_score 必须返回 0 到 100 的整数（例如 70），不要返回 0 到 1 的小数（例如 0.7）。
只返回符合要求 JSON Schema 的 JSON，不要返回 Markdown、解释或代码围栏。"""


def _model_dump(model: BaseModel) -> dict[str, Any]:
    return model.model_dump() if hasattr(model, "model_dump") else model.dict()


def _parse_json(raw: str) -> Any:
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        starts = [index for index in (cleaned.find("{"), cleaned.find("[")) if index >= 0]
        if not starts:
            raise
        start = min(starts)
        end = max(cleaned.rfind("}"), cleaned.rfind("]"))
        if end <= start:
            raise
        return json.loads(cleaned[start : end + 1])


def _normalise_model_payload(payload: Any, schema: type[ModelT]) -> Any:
    """Handle common provider drift before strict Pydantic validation."""
    if schema is not Analysis or not isinstance(payload, dict) or "overall_score" not in payload:
        return payload
    value = payload["overall_score"]
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return payload
    # Providers sometimes express a percentage as a confidence-like ratio.
    if 0 <= numeric <= 1:
        numeric *= 100
    payload = dict(payload)
    payload["overall_score"] = round(numeric)
    return payload


def _schema_contract(schema: type[BaseModel]) -> str:
    """Short, provider-friendly contracts are more reliable than an unconstrained JSON request."""
    contracts = {
        "ResumeProfile": "{" 
            "name: string, summary: string, skills: string[], experiences: Experience[], "
            "education: Education[], projects: Project[], evidence: string[]"
            "}",
        "JobProfile": "{title: string, company: string, summary: string, must_have: string[], nice_to_have: string[], responsibilities: string[], keywords: string[]}",
        "Analysis": (
            "{overall_score: integer 0-100, conclusion: string, "
            "matched_requirements: [{requirement: string, status: 'matched'|'partial'|'missing', evidence: [{text: string, source: 'resume'|'job'|'user'}], note: string}], "
            "missing_requirements: same MatchItem[], "
            "strengths: [{title: string, detail: string, evidence: EvidenceItem[]}], "
            "gaps: same Insight[], "
            "recommendations: [{priority: 'high'|'medium'|'low', action: string, rationale: string, evidence: EvidenceItem[], needs_confirmation: boolean}], "
            "next_steps: string[]}"
        ),
        "RewriteResponse": "{application_id: string, language: 'zh'|'en', summary: string, bullets: RewriteItem[]}",
        "AnswerResponse": "{session_id: string, question_id: string, score: integer 0-10, strengths: string[], improvements: string[], follow_up: string, next_question: InterviewQuestion|null, completed: boolean}",
        "_InterviewQuestionEnvelope": "{questions: InterviewQuestion[]}",
    }
    return contracts.get(schema.__name__, "{" + ", ".join(schema.model_fields) + "}")


class MockLLM:
    """Deterministic provider used for local demos and tests."""

    KNOWN_SKILLS = [
        "Python", "Java", "JavaScript", "TypeScript", "React", "Vue", "FastAPI", "Spring",
        "SQL", "MySQL", "PostgreSQL", "Docker", "Git", "Linux", "机器学习", "深度学习",
        "数据分析", "爬虫", "LangChain", "LangGraph", "PyTorch", "TensorFlow", "Redis",
    ]

    def extract_resume(self, text: str) -> ResumeProfile:
        lines = [line.strip(" -•\t") for line in text.splitlines() if line.strip()]
        skills = [skill for skill in self.KNOWN_SKILLS if skill.lower() in text.lower()]
        evidence = lines[:20]
        projects = []
        for index, line in enumerate(lines):
            if any(marker in line.lower() for marker in ("项目", "project", "系统", "平台", "assistant")):
                projects.append({"name": line[:80], "description": line, "technologies": skills[:5], "bullets": lines[index + 1 : index + 3]})
        if not projects and lines:
            projects = [{"name": "简历经历", "description": lines[0], "technologies": skills[:5], "bullets": lines[1:4]}]
        return ResumeProfile(
            name=lines[0][:40] if lines else "",
            summary=" ".join(lines[:2]),
            skills=skills,
            projects=projects,
            evidence=evidence,
        )

    def extract_job(self, text: str) -> JobProfile:
        lines = [line.strip(" -•\t") for line in text.splitlines() if line.strip()]
        skills = [skill for skill in self.KNOWN_SKILLS if skill.lower() in text.lower()]
        must_have = []
        nice_to_have = []
        for line in lines:
            low = line.lower()
            if any(token in low for token in ("优先", "加分", "preferred", "bonus", "nice to have")):
                nice_to_have.append(line[:160])
            elif any(skill.lower() in low for skill in skills) or any(token in low for token in ("要求", "responsib", "负责", "熟悉", "经验")):
                must_have.append(line[:160])
        if not must_have:
            must_have = lines[: min(6, len(lines))]
        return JobProfile(
            title=lines[0][:80] if lines else "目标职位",
            summary=" ".join(lines[:2]),
            must_have=must_have[:10],
            nice_to_have=nice_to_have[:10],
            responsibilities=lines[1:6],
            keywords=skills,
        )

    def match(self, resume: ResumeProfile, job: JobProfile, retrieved: list[RetrievedExperience] | None = None) -> Analysis:
        corpus = " ".join(resume.evidence + resume.skills + [b for p in resume.projects for b in p.bullets]).lower()
        corpus += " " + " ".join(item.text for item in (retrieved or []))
        matched: list[dict[str, Any]] = []
        missing: list[dict[str, Any]] = []
        for requirement in job.must_have:
            words = [word for word in re.findall(r"[\w\u4e00-\u9fff+#.]+", requirement.lower()) if len(word) > 1]
            hits = [word for word in words if word in corpus]
            status = "matched" if hits and len(hits) >= max(1, len(words) // 2) else ("partial" if hits else "missing")
            evidence = [{"text": next((line for line in resume.evidence if any(hit in line.lower() for hit in hits)), ""), "source": "resume"}] if hits else []
            item = {"requirement": requirement, "status": status, "evidence": [e for e in evidence if e["text"]], "note": "已在简历中找到相关证据。" if status == "matched" else "建议补充可验证的项目或经历。"}
            (matched if status in {"matched", "partial"} else missing).append(item)
        total = len(job.must_have)
        points = sum(1 if item["status"] == "matched" else 0.5 for item in matched)
        score = round(points / total * 100) if total else 0
        evidence_lines = [item.text.splitlines()[0] for item in (retrieved or [])[:2] if item.text] or resume.evidence[:2]
        strengths = [{"title": "已有相关能力", "detail": f"简历中识别到：{', '.join(resume.skills[:6]) or '暂未识别明确技能'}。检索到 {len(retrieved or [])} 条相关经历。", "evidence": [{"text": line, "source": "resume"} for line in evidence_lines]}]
        gaps = [{"title": "职位要求缺口", "detail": item["requirement"], "evidence": [{"text": item["requirement"], "source": "job"}]} for item in missing[:5]]
        recommendations = []
        for item in missing[:5]:
            recommendations.append({"priority": "high", "action": f"补充与“{item['requirement']}”相关的真实项目、课程或成果。", "rationale": "该要求尚未找到简历证据。", "evidence": [{"text": item["requirement"], "source": "job"}], "needs_confirmation": True})
        for item in matched[:3]:
            if item["evidence"]:
                recommendations.append({"priority": "medium", "action": "将已有经历改写为包含动作、技术和结果的 bullet point。", "rationale": "已有相关证据，但当前表达可以更具体。", "evidence": item["evidence"], "needs_confirmation": False})
        return Analysis(
            overall_score=score,
            conclusion="匹配度较高，可以优先申请。" if score >= 70 else ("具备部分基础，建议先补齐高优先级缺口。" if score >= 40 else "当前匹配度偏低，建议补充经历或寻找更合适的职位。"),
            matched_requirements=matched,
            missing_requirements=missing,
            strengths=strengths,
            gaps=gaps,
            recommendations=recommendations,
            next_steps=["先处理高优先级缺口", "把已有项目改写成结果导向的 bullet point", "准备一个与职位要求最相关的项目案例"],
        )

    def rewrite(self, resume: ResumeProfile, job: JobProfile, analysis: Analysis, language: str, retrieved: list[RetrievedExperience] | None = None) -> RewriteResponse:
        items = []
        candidates = retrieved or []
        if candidates:
            for item in candidates[:4]:
                candidate_lines = [line.strip() for line in item.text.splitlines() if line.strip()]
                source = next((line for line in candidate_lines if line in resume.evidence), "")
                if item.source == "profile" and not source:
                    source = candidate_lines[0] if candidate_lines else ""
                if not source:
                    continue
                rewritten = f"{('Built' if language == 'en' else '负责')}{item.title}，{('using ' + ', '.join(resume.skills[:4]) + ', ' if language == 'en' and resume.skills else '使用' + '、'.join(resume.skills[:4]) + '，' if language != 'en' and resume.skills else '')}{('delivering role-relevant impact with evidence from the project.' if language == 'en' else '完成核心工作，并将成果与目标岗位要求对应。')}"
                evidence_source = "user" if item.source == "profile" else "resume"
                items.append({"original": source, "rewritten": rewritten, "evidence": [{"text": source, "source": evidence_source}], "needs_confirmation": True})
        for project in resume.projects[:4] if not candidates else []:
            source = project.bullets[0] if project.bullets else project.description
            if not source:
                continue
            if language == "en":
                rewritten = f"Built {project.name or 'a project'} using {', '.join(project.technologies[:4]) or 'relevant technologies'}, translating the work into measurable, role-relevant impact."
            else:
                rewritten = f"负责{project.name or '项目'}，使用{', '.join(project.technologies[:4]) or '相关技术'}完成核心工作，并将成果与目标岗位要求对应。"
            items.append({"original": source, "rewritten": rewritten, "evidence": [{"text": source, "source": "resume"}], "needs_confirmation": True})
        if not items and resume.evidence:
            source = resume.evidence[0]
            items.append({"original": source, "rewritten": source, "evidence": [{"text": source, "source": "resume"}], "needs_confirmation": True})
        return RewriteResponse(application_id="", language=language, summary="以下内容基于简历中的已有经历生成，请补充真实数据后再使用。", bullets=items)

    def interview_questions(self, job: JobProfile, analysis: Analysis, count: int) -> list[InterviewQuestion]:
        focuses = [item.requirement for item in analysis.matched_requirements[:count]] + [item.requirement for item in analysis.missing_requirements[:count]]
        if not focuses:
            focuses = job.keywords or ["项目经验"]
        return [InterviewQuestion(id=f"q{index + 1}", question=f"请结合你的经历，说明你如何应对：{focus}？", focus=focus, ideal_points=["说明具体背景", "解释个人负责的工作", "给出结果或复盘"]) for index, focus in enumerate(focuses[:count])]

    def evaluate_answer(self, answer: str, question: InterviewQuestion, next_question: InterviewQuestion | None) -> AnswerResponse:
        score = min(10, max(2, len(answer.strip()) // 25 + (2 if any(token in answer for token in ("结果", "提升", "%", "负责")) else 0)))
        return AnswerResponse(session_id="", question_id=question.id, score=score, strengths=["回答与当前问题相关。"], improvements=["补充具体行动、结果和可验证数据。"] if score < 8 else ["继续保持结构清晰，并说明复盘。"], follow_up="能否进一步说明你个人承担的部分？", next_question=next_question, completed=next_question is None)


class LLMClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.mock = MockLLM()

    async def _call_with_status(self, instruction: str, schema: type[ModelT]) -> tuple[ModelT, bool]:
        self.settings.validate_llm()
        url = self.settings.llm_base_url.rstrip("/")
        if not url.endswith("/chat/completions"):
            url += "/chat/completions"
        payload = {
            "model": self.settings.llm_model,
            "temperature": 0.2,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": instruction}],
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self.settings.llm_api_key}", "Content-Type": "application/json"}
        last_error: Exception | None = None
        contract = _schema_contract(schema)
        base_instruction = (
            f"任务输出类型：{schema.__name__}\n"
            f"必须严格返回以下 JSON 结构（字段名不能改，数组字段即使为空也必须保留）：{contract}\n"
            f"{instruction}"
        )
        payload["messages"][1]["content"] = base_instruction
        async with httpx.AsyncClient(timeout=self.settings.request_timeout_seconds) as client:
            for attempt in range(2):
                try:
                    response = await client.post(url, headers=headers, json=payload)
                    response.raise_for_status()
                    raw = response.json()["choices"][0]["message"]["content"]
                    parsed = _parse_json(raw)
                    result = schema.model_validate(_normalise_model_payload(parsed, schema))
                    return result, attempt == 1
                except Exception as exc:
                    last_error = exc
                    logger.warning("DeepSeek structured output failed for %s (attempt %d): %s", schema.__name__, attempt + 1, exc)
                    payload["messages"].append(
                        {
                            "role": "user",
                            "content": (
                                f"上一条输出无法通过 {schema.__name__} 校验。请重新生成完整 JSON。\n"
                                f"校验错误：{exc}\n"
                                f"严格结构：{contract}\n"
                                "不要输出 candidate_name、next_step 等未定义字段，不能省略必填字段。"
                            ),
                        }
                    )
        raise StructuredOutputError(schema.__name__) from last_error

    async def _call(self, instruction: str, schema: type[ModelT]) -> ModelT:
        result, _ = await self._call_with_status(instruction, schema)
        return result

    async def extract_resume(self, text: str) -> ResumeProfile:
        if self.settings.mock_llm:
            return self.mock.extract_resume(text)
        return await self._call(f"请从以下简历提取 ResumeProfile。所有 evidence 必须是原文。\n\n{text}", ResumeProfile)

    async def extract_job(self, text: str) -> JobProfile:
        if self.settings.mock_llm:
            return self.mock.extract_job(text)
        return await self._call(f"请从以下职位描述提取 JobProfile。\n\n{text}", JobProfile)

    async def match(self, resume: ResumeProfile, job: JobProfile, retrieved: list[RetrievedExperience] | None = None) -> Analysis:
        if self.settings.mock_llm:
            return self.mock.match(resume, job, retrieved)
        try:
            result, repaired = await self._call_with_status(
                f"请比较以下 ResumeProfile 和 JobProfile，输出完整 Analysis。务必优先使用检索到的经历作为候选人证据，不能补写其中没有的信息。\nResumeProfile: {json.dumps(_model_dump(resume), ensure_ascii=False)}\nJobProfile: {json.dumps(_model_dump(job), ensure_ascii=False)}\nRetrieved experiences: {json.dumps([_model_dump(item) for item in (retrieved or [])], ensure_ascii=False)}",
                Analysis,
            )
            result.analysis_source = "deepseek_repaired" if repaired else "deepseek"
            return result
        except StructuredOutputError:
            # The deterministic matcher keeps the user workflow available when a provider
            # ignores the contract twice. The failed provider details are already logged.
            result = self.mock.match(resume, job, retrieved)
            result.analysis_source = "local_fallback"
            result.warning = "DeepSeek 输出格式异常，已使用本地规则完成匹配分析。"
            return result

    async def rewrite(self, resume: ResumeProfile, job: JobProfile, analysis: Analysis, language: str, retrieved: list[RetrievedExperience] | None = None) -> RewriteResponse:
        if self.settings.mock_llm:
            return self.mock.rewrite(resume, job, analysis, language, retrieved)
        return await self._call(f"请根据已有证据生成 RewriteResponse。目标语言为 {language}。只改写检索到的最相关经历，所有 evidence 必须来自简历原文。\nResumeProfile: {json.dumps(_model_dump(resume), ensure_ascii=False)}\nJobProfile: {json.dumps(_model_dump(job), ensure_ascii=False)}\nAnalysis: {json.dumps(_model_dump(analysis), ensure_ascii=False)}\nRetrieved experiences: {json.dumps([_model_dump(item) for item in (retrieved or [])], ensure_ascii=False)}", RewriteResponse)

    async def interview_questions(self, job: JobProfile, analysis: Analysis, count: int) -> list[InterviewQuestion]:
        if self.settings.mock_llm:
            return self.mock.interview_questions(job, analysis, count)
        result = await self._call(
            f"请为以下求职者生成 {count} 个面试问题。只返回 JSON 对象 {{\"questions\": [...]}}，每个问题符合 InterviewQuestion。\nJobProfile: {json.dumps(_model_dump(job), ensure_ascii=False)}\nAnalysis: {json.dumps(_model_dump(analysis), ensure_ascii=False)}",
            _InterviewQuestionEnvelope,
        )
        return result.questions[:count]

    async def evaluate_answer(self, answer: str, question: InterviewQuestion, next_question: InterviewQuestion | None) -> AnswerResponse:
        if self.settings.mock_llm:
            return self.mock.evaluate_answer(answer, question, next_question)
        result = await self._call(
            f"请评估候选人对下面问题的回答，输出 AnswerResponse。session_id 留空，question_id 使用 {question.id}，next_question 保持为 {json.dumps(_model_dump(next_question), ensure_ascii=False) if next_question else 'null'}。\n问题: {question.question}\n回答: {answer}",
            AnswerResponse,
        )
        return result


class _InterviewQuestionEnvelope(BaseModel):
    questions: list[InterviewQuestion] = []
