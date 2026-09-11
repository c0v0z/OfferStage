import httpx
import pytest

from app.config import Settings
from app.database import Database
from app.main import create_app


@pytest.mark.asyncio
async def test_api_workflow(tmp_path):
    settings = Settings(database_path=str(tmp_path / "api.db"), mock_llm=True)
    app = create_app(settings, Database(settings.database_path))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        form = {
            "resume_text": "李四\n项目：数据分析平台\n使用 Python 和 SQL 完成报表开发",
            "jd_text": "数据分析实习生\n要求：Python、SQL\n负责数据分析",
        }
        response = await client.post("/api/analyze", data=form)
        assert response.status_code == 200
        application = response.json()
        application_id = application["id"]

        rewrite = await client.post("/api/rewrite", json={"application_id": application_id})
        assert rewrite.status_code == 200
        assert rewrite.json()["application_id"] == application_id

        interview = await client.post("/api/interview/start", json={"application_id": application_id, "count": 2})
        assert interview.status_code == 200
        session = interview.json()
        answer = await client.post(
            f"/api/interview/{session['session_id']}/answer",
            json={"question_id": session["current_question"]["id"], "answer": "我负责数据分析，使用 Python 完成了项目并验证了结果。"},
        )
        assert answer.status_code == 200
        assert (await client.get("/api/history")).json()[0]["id"] == application_id


@pytest.mark.asyncio
async def test_profile_and_retrieval_api(tmp_path):
    settings = Settings(database_path=str(tmp_path / "profile.db"), mock_llm=True, embedding_mode="local")
    app = create_app(settings, Database(settings.database_path))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        profile = await client.post("/api/profiles", json={"name": "后端求职档案"})
        assert profile.status_code == 200
        profile_id = profile.json()["id"]
        experience = await client.post(
            f"/api/profiles/{profile_id}/experiences",
            json={"kind": "project", "title": "数据平台", "text": "使用 Python 和 SQL 构建数据分析平台"},
        )
        assert experience.status_code == 200
        results = await client.get(f"/api/profiles/{profile_id}/retrieve", params={"query": "Python SQL 数据分析"})
        assert results.status_code == 200
        assert results.json()["results"][0]["title"] == "数据平台"
