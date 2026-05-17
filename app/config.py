"""
Centralized configuration module for Yahli AI.

All environment variables are loaded and validated here.
Other modules should import from this file instead of using os.getenv() directly.
"""

import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    """Centralized configuration loaded from environment variables."""

    # --- Google Gemini API ---
    GOOGLE_GEMINI_API_KEY: str = os.getenv("GOOGLE_GEMINI_API_KEY", "")

    # --- Google Cloud Services ---
    GOOGLE_API_KEY: str = os.getenv("GOOGLE_API_KEY", "")
    SEARCH_ENGINE_ID: str = os.getenv("SEARCH_ENGINE_ID", "")

    # --- Telegram ---
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    ADMIN_CHAT_ID: str = os.getenv("ADMIN_CHAT_ID", "")

    # --- LLM Provider ---
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "google")

    # --- LLM Models ---
    GEMINI_MODEL_NAME: str = os.getenv("GEMINI_MODEL_NAME", "gemma-4-31b-it")
    GEMINI_MODEL_FOR_COMPLEX_NAME: str = os.getenv("GEMINI_MODEL_FOR_COMPLEX_NAME", "gemma-4-31b-it")
    GEMINI_VISION_MODEL: str = os.getenv("GEMINI_VISION_MODEL", "gemma-4-31b-it")
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "models/gemini-embedding-001")

    # --- Ollama ---
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "gemma-4-31b-it")

    # --- Memory ---
    MEMORY_DEDUP_SIM: float = float(os.getenv("MEMORY_DEDUP_SIM", "0.87"))
    MEMORY_MAX_CACHE_ITEMS: int = int(os.getenv("MEMORY_MAX_CACHE_ITEMS", "20000"))
    MEMORY_MAX_SUMMARY_CHARS: int = int(os.getenv("MEMORY_MAX_SUMMARY_CHARS", "1200"))
    MEMORY_RERANK_TOP_K: int = int(os.getenv("MEMORY_RERANK_TOP_K", "6"))

    # --- Debug ---
    YAHLI_API_LOGGING: bool = os.getenv("YAHLI_API_LOGGING", "true").lower() == "true"

    @classmethod
    def validate(cls) -> None:
        """Validate that all required environment variables are set."""
        missing = []
        if not cls.GOOGLE_GEMINI_API_KEY:
            missing.append("GOOGLE_GEMINI_API_KEY")
        if not cls.GOOGLE_API_KEY:
            missing.append("GOOGLE_API_KEY")
        if not cls.SEARCH_ENGINE_ID:
            missing.append("SEARCH_ENGINE_ID")
        if not cls.TELEGRAM_BOT_TOKEN:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not cls.ADMIN_CHAT_ID:
            missing.append("ADMIN_CHAT_ID")
        if missing:
            raise ValueError(
                f"Missing required environment variables: {', '.join(missing)}\n"
                f"Please copy .env.example to .env and fill in your values."
            )


# Module-level convenience exports
LLM_PROVIDER = Config.LLM_PROVIDER
GEMINI_MODEL_NAME = Config.GEMINI_MODEL_NAME
GEMINI_MODEL_FOR_COMPLEX_NAME = Config.GEMINI_MODEL_FOR_COMPLEX_NAME
GEMINI_VISION_MODEL = Config.GEMINI_VISION_MODEL
EMBEDDING_MODEL = Config.EMBEDDING_MODEL
OLLAMA_BASE_URL = Config.OLLAMA_BASE_URL
OLLAMA_MODEL = Config.OLLAMA_MODEL
GOOGLE_GEMINI_API_KEY = Config.GOOGLE_GEMINI_API_KEY
GOOGLE_API_KEY = Config.GOOGLE_API_KEY
SEARCH_ENGINE_ID = Config.SEARCH_ENGINE_ID
TELEGRAM_BOT_TOKEN = Config.TELEGRAM_BOT_TOKEN
ADMIN_CHAT_ID = Config.ADMIN_CHAT_ID
