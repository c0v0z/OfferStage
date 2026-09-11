import hashlib
import json
import math
import re
import uuid
from typing import Any

import httpx

from .config import Settings
from .database import Database
from .schemas import ExperienceRecord, ExperienceCreateRequest, RetrievedExperience


TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_+#.-]+|[\u4e00-\u9fff]")


def _normalise_vector(values: list[float]) -> list[float]:
    length = math.sqrt(sum(value * value for value in values))
    return [value / length for value in values] if length else values


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        return 0.0
    return max(0.0, min(1.0, sum(a * b for a, b in zip(left, right))))


def _tokens(text: str) -> set[str]:
    return {token.lower() for token in TOKEN_PATTERN.findall(text or "") if len(token.strip()) > 0}


def _lexical_score(query: str, text: str) -> float:
    query_tokens = _tokens(query)
    if not query_tokens:
        return 0.0
    return len(query_tokens & _tokens(text)) / len(query_tokens)


class EmbeddingProvider:
    """Provides API embeddings when configured, with a deterministic local fallback."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.model = settings.embedding_model if settings.embedding_mode in {"api", "auto"} else "local-hash-v1"

    def _local_embed(self, text: str) -> list[float]:
        dimension = max(32, self.settings.embedding_dimension)
        values = [0.0] * dimension
        tokens = TOKEN_PATTERN.findall(text.lower())
        tokens += [tokens[index] + tokens[index + 1] for index in range(len(tokens) - 1)]
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % dimension
            sign = 1.0 if digest[4] & 1 else -1.0
            values[index] += sign
        return _normalise_vector(values)

    async def _api_embed(self, text: str) -> list[float]:
        self.settings.validate_embedding_api()
        url = self.settings.embedding_base_url.rstrip("/")
        if not url.endswith("/embeddings"):
            url += "/embeddings"
        headers = {"Authorization": f"Bearer {self.settings.embedding_api_key}", "Content-Type": "application/json"}
        payload = {"model": self.settings.embedding_model, "input": text}
        async with httpx.AsyncClient(timeout=self.settings.request_timeout_seconds) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            values = response.json()["data"][0]["embedding"]
            if not isinstance(values, list) or not values:
                raise RuntimeError("Embedding 接口返回了空向量。")
            return _normalise_vector([float(value) for value in values])

    async def embed(self, text: str) -> list[float]:
        if self.settings.embedding_mode == "local":
            return self._local_embed(text)
        try:
            vector = await self._api_embed(text)
            self.model = self.settings.embedding_model
            return vector
        except Exception:
            if self.settings.embedding_mode == "api":
                raise
            self.model = "local-hash-v1"
            return self._local_embed(text)


class RetrievalService:
    def __init__(self, database: Database, settings: Settings):
        self.database = database
        self.embedder = EmbeddingProvider(settings)

    async def ensure_profile(self, profile_id: str | None, name: str) -> str:
        if profile_id:
            if not self.database.get_profile(profile_id):
                raise KeyError("找不到对应的候选人档案。")
            return profile_id
        new_id = str(uuid.uuid4())
        self.database.create_profile(new_id, name or "我的求职档案")
        return new_id

    async def add_experience(
        self,
        profile_id: str,
        kind: str,
        title: str,
        text: str,
        organization: str = "",
        period: str = "",
        source: str = "manual",
    ) -> ExperienceRecord:
        if not self.database.get_profile(profile_id):
            raise KeyError("找不到对应的候选人档案。")
        experience_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{profile_id}:{kind}:{title}:{organization}:{period}:{text}"))
        created_at = self.database.add_experience(experience_id, profile_id, kind, title, organization, period, text, source)
        vector = await self.embedder.embed(f"{title}\n{organization}\n{period}\n{text}")
        self.database.save_embedding(experience_id, profile_id, self.embedder.model, len(vector), vector)
        return ExperienceRecord(id=experience_id, profile_id=profile_id, kind=kind, title=title, organization=organization, period=period, text=text, source=source, created_at=created_at)

    async def index_resume(self, profile_id: str, resume_text: str, resume: Any) -> list[ExperienceRecord]:
        records: list[tuple[str, str, str, str, str]] = [("resume", "简历原文", resume_text, "", "")]
        for project in resume.projects:
            text = "\n".join([project.description, *project.bullets, "技术：" + ", ".join(project.technologies)]).strip()
            if text:
                records.append(("project", project.name or "未命名项目", text, "", ""))
        for experience in resume.experiences:
            text = "\n".join(experience.bullets).strip()
            if text:
                records.append(("experience", experience.title or "工作经历", text, experience.organization, experience.period))
        for education in resume.education:
            text = " ".join(item for item in (education.degree, education.field) if item)
            if text:
                records.append(("education", education.school or "教育经历", text, education.school, education.period))
        if resume.skills:
            records.append(("skill", "技能清单", "、".join(resume.skills), "", ""))
        result = []
        for kind, title, text, organization, period in records:
            result.append(await self.add_experience(profile_id, kind, title, text, organization, period, "resume"))
        return result

    async def search(self, profile_id: str, query: str, limit: int = 8) -> list[RetrievedExperience]:
        query_vector = await self.embedder.embed(query)
        rows = self.database.list_embeddings(profile_id)
        scored: list[RetrievedExperience] = []
        for row in rows:
            try:
                vector = json.loads(row["vector_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            semantic_score = _cosine(query_vector, vector)
            lexical_score = _lexical_score(query, f"{row['title']}\n{row['organization']}\n{row['text']}")
            score = min(1.0, 0.6 * semantic_score + 0.4 * lexical_score)
            scored.append(RetrievedExperience(id=row["id"], profile_id=profile_id, kind=row["kind"], title=row["title"], text=row["text"], score=round(score, 4), source="profile" if row["source"] == "manual" else "resume"))
        scored.sort(key=lambda item: item.score, reverse=True)
        return scored[: max(1, min(limit, 20))]

    def list_profiles(self, limit: int = 50) -> list[dict[str, Any]]:
        return [dict(row) for row in self.database.list_profiles(limit)]

    def list_experiences(self, profile_id: str) -> list[ExperienceRecord]:
        return [ExperienceRecord(id=row["id"], profile_id=row["profile_id"], kind=row["kind"], title=row["title"], organization=row["organization"], period=row["period"], text=row["text"], source=row["source"], created_at=row["created_at"]) for row in self.database.list_experiences(profile_id)]
