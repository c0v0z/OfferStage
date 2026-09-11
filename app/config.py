from dataclasses import dataclass
import os

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Settings:
    database_path: str = os.getenv("DATABASE_PATH", "offersage.db")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "deepseek-chat")
    mock_llm: bool = os.getenv("MOCK_LLM", "true").strip().lower() in {"1", "true", "yes", "on"}
    request_timeout_seconds: float = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "60"))
    embedding_mode: str = os.getenv("EMBEDDING_MODE", "local").strip().lower()
    embedding_base_url: str = os.getenv("EMBEDDING_BASE_URL", os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1"))
    embedding_api_key: str = os.getenv("EMBEDDING_API_KEY", os.getenv("LLM_API_KEY", ""))
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
    embedding_dimension: int = int(os.getenv("EMBEDDING_DIMENSION", "256"))

    def validate_llm(self) -> None:
        if self.mock_llm:
            return
        if not self.llm_api_key:
            raise RuntimeError("未配置 LLM_API_KEY。请复制 .env.example 为 .env 并填写模型 API Key，或将 MOCK_LLM=true。")
        if not self.llm_base_url:
            raise RuntimeError("未配置 LLM_BASE_URL。")

    def validate_embedding_api(self) -> None:
        if self.embedding_mode not in {"api", "auto"}:
            return
        if not self.embedding_api_key:
            raise RuntimeError("未配置 EMBEDDING_API_KEY。")
        if not self.embedding_base_url:
            raise RuntimeError("未配置 EMBEDDING_BASE_URL。")


settings = Settings()
