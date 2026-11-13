"""Runtime configuration, read from environment variables (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: str = ".env") -> None:
    """Tiny .env loader so the project has no extra dependency."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


@dataclass(frozen=True)
class Settings:
    llm_provider: str
    aws_region: str
    supervisor_model_id: str
    worker_model_id: str
    data_dir: Path
    approval_mode: str
    payment_approval_threshold: float
    approval_ttl_hours: float
    internal_email_domain: str
    max_steps: int
    llm_temperature: float | None
    opensanctions_api_key: str | None
    slack_webhook_url: str | None

    @property
    def checkpoint_db(self) -> Path:
        return self.data_dir / "checkpoints.sqlite"

    @property
    def history_db(self) -> Path:
        return self.data_dir / "history.sqlite"

    @property
    def systems_db(self) -> Path:
        return self.data_dir / "mock_systems.sqlite"


def get_settings() -> Settings:
    _load_dotenv()
    data_dir = Path(os.getenv("DATA_DIR", "./data"))
    data_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        llm_provider=os.getenv("LLM_PROVIDER", "fake").lower(),
        aws_region=os.getenv("AWS_REGION", "us-east-1"),
        supervisor_model_id=os.getenv("SUPERVISOR_MODEL_ID", "us.anthropic.claude-sonnet-5-5"),
        worker_model_id=os.getenv("WORKER_MODEL_ID", "us.anthropic.claude-haiku-5-5"),
        data_dir=data_dir,
        approval_mode=os.getenv("APPROVAL_MODE", "policy").lower(),
        payment_approval_threshold=float(os.getenv("PAYMENT_APPROVAL_THRESHOLD", "1000")),
        approval_ttl_hours=float(os.getenv("APPROVAL_TTL_HOURS", "24")),
        internal_email_domain=os.getenv("INTERNAL_EMAIL_DOMAIN", "example.com"),
        max_steps=int(os.getenv("MAX_STEPS", "25")),
        # Newer Claude models reject `temperature`; only send it when explicitly set.
        llm_temperature=float(os.environ["LLM_TEMPERATURE"]) if os.getenv("LLM_TEMPERATURE") else None,
        opensanctions_api_key=os.getenv("OPENSANCTIONS_API_KEY") or None,
        slack_webhook_url=os.getenv("SLACK_WEBHOOK_URL") or None,
    )
