from io import BytesIO

import pytest
from docx import Document
from reportlab.pdfgen import canvas

from app.agent import OfferSageAgent
from app.config import Settings
from app.database import Database
from app.llm import LLMClient, StructuredOutputError, _normalise_model_payload, _parse_json
from app.schemas import Analysis, JobProfile, ResumeProfile
from app.parsers import extract_text
from app.retrieval import RetrievalService


def make_pdf(text: str) -> bytes:
    output = BytesIO()
    pdf = canvas.Canvas(output)
    pdf.drawString(72, 760, text)
    pdf.save()
    return output.getvalue()


def make_docx(text: str) -> bytes:
    document = Document()
    document.add_paragraph(text)
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def test_extract_supported_documents():
    assert extract_text("张三\nPython\n".encode(), "resume.txt") == "张三\nPython"
    assert "Python" in extract_text(make_docx("Python 项目"), "resume.docx")
    assert "Python project" in extract_text(make_pdf("Python project"), "resume.pdf")


def test_parse_json_handles_code_fences_and_noise():
    assert _parse_json("```json\n{\"ok\": true}\n```") == {"ok": True}
    assert _parse_json("模型说明\n{\"ok\": true}\n") == {"ok": True}


def test_normalise_fractional_analysis_score():
    payload = _normalise_model_payload({"overall_score": 0.7, "conclusion": "ok"}, Analysis)
    assert payload["overall_score"] == 70
    payload = _normalise_model_payload({"overall_score": 72, "conclusion": "ok"}, Analysis)
    assert payload["overall_score"] == 72


@pytest.mark.asyncio
async def test_agent_end_to_end(tmp_path):
    settings = Settings(database_path=str(tmp_path / "test.db"), mock_llm=True)
    database = Database(settings.database_path)
    agent = OfferSageAgent(database, LLMClient(settings))
    resume = "张三\n教育背景：计算机科学\n项目：求职助手\n使用 Python、FastAPI、SQL 构建系统\n负责数据分析和接口开发"
    job = "Python 开发实习生\n要求：Python、FastAPI、SQL\n负责后端接口开发\n优先：Docker"

    application = await agent.analyze(resume, job)
    assert application.id
    assert application.analysis.overall_score > 0
    assert all(item.evidence for item in application.analysis.strengths)

    rewrite = await agent.rewrite(application.id)
    assert rewrite.application_id == application.id
    assert rewrite.bullets
    assert all(item.evidence for item in rewrite.bullets)

    interview = await agent.start_interview(application.id, 2)
    assert interview.current_question is not None
    first = await agent.answer_interview(interview.session_id, interview.current_question.id, "我负责接口开发，使用 FastAPI 完成了核心功能，结果是项目可以稳定运行。")
    assert first.score >= 2
    assert first.next_question is not None
    second = await agent.answer_interview(interview.session_id, first.next_question.id, "我先拆解需求，再用 SQL 设计数据结构，并通过测试验证结果。")
    assert second.completed is True
    assert len(agent.history()) == 1


@pytest.mark.asyncio
async def test_llm_retries_invalid_json(monkeypatch):
    import app.llm as llm_module

    class FakeResponse:
        def __init__(self, content):
            self.content = content

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": self.content}}]}

    class FakeClient:
        calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def post(self, *_args, **_kwargs):
            self.calls += 1
            return FakeResponse("not json" if self.calls == 1 else "{}")

    fake = FakeClient()
    monkeypatch.setattr(llm_module.httpx, "AsyncClient", lambda **_: fake)
    client = LLMClient(Settings(mock_llm=False, llm_api_key="test", llm_base_url="http://example.test/v1"))
    result = await client.extract_resume("张三")
    assert result.name == ""
    assert fake.calls == 2


@pytest.mark.asyncio
async def test_analysis_contract_retries_and_marks_repair(monkeypatch):
    import app.llm as llm_module

    class FakeResponse:
        def __init__(self, content): self.content = content
        def raise_for_status(self): return None
        def json(self): return {"choices": [{"message": {"content": self.content}}]}

    valid = '{"overall_score": 0.7, "conclusion": "可以申请", "matched_requirements": [], "missing_requirements": [], "strengths": [], "gaps": [], "recommendations": [], "next_steps": []}'

    class FakeClient:
        calls = 0
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return False
        async def post(self, *_args, **_kwargs):
            self.calls += 1
            return FakeResponse('{"candidate_name":"陈同学","next_step":"沟通确认"}' if self.calls == 1 else valid)

    fake = FakeClient()
    monkeypatch.setattr(llm_module.httpx, "AsyncClient", lambda **_: fake)
    client = LLMClient(Settings(mock_llm=False, llm_api_key="test", llm_base_url="http://example.test/v1"))
    result = await client.match(ResumeProfile(), JobProfile())
    assert result.overall_score == 70
    assert result.analysis_source == "deepseek_repaired"
    assert fake.calls == 2


@pytest.mark.asyncio
async def test_analysis_contract_falls_back_without_exposing_provider_error(monkeypatch):
    import app.llm as llm_module

    class FakeResponse:
        def raise_for_status(self): return None
        def json(self): return {"choices": [{"message": {"content": '{"candidate_name":"陈同学","next_step":"沟通确认"}'}}]}

    class FakeClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return False
        async def post(self, *_args, **_kwargs): return FakeResponse()

    monkeypatch.setattr(llm_module.httpx, "AsyncClient", lambda **_: FakeClient())
    client = LLMClient(Settings(mock_llm=False, llm_api_key="test", llm_base_url="http://example.test/v1"))
    result = await client.match(ResumeProfile(skills=["Python"], evidence=["Python 项目"]), JobProfile(must_have=["Python"]))
    assert result.analysis_source == "local_fallback"
    assert result.warning == "DeepSeek 输出格式异常，已使用本地规则完成匹配分析。"


@pytest.mark.asyncio
async def test_profile_experience_index_and_retrieval(tmp_path):
    settings = Settings(database_path=str(tmp_path / "retrieval.db"), mock_llm=True, embedding_mode="local", embedding_dimension=128)
    database = Database(settings.database_path)
    service = RetrievalService(database, settings)
    profile_id = await service.ensure_profile(None, "软件开发求职档案")
    await service.add_experience(profile_id, "project", "推荐系统", "使用 Python 和 SQL 构建职位推荐服务")
    await service.add_experience(profile_id, "project", "视觉检测", "使用 PyTorch 训练缺陷检测模型")
    await service.add_experience(profile_id, "experience", "社团运营", "负责活动组织和公众号内容编辑")

    results = await service.search(profile_id, "Python SQL 后端开发", limit=2)
    assert len(results) == 2
    assert results[0].title == "推荐系统"
    assert results[0].score >= results[1].score
    assert all(0 <= result.score <= 1 for result in results)
    assert len(service.list_experiences(profile_id)) == 3
