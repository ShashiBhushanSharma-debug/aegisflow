import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    PROJECT_NAME: str = "AegisFlow Fintech AI Engine"
    API_V1_STR: str = "/api/v1"

    SUPABASE_URL: str = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "")

    RAZORPAY_KEY_ID: str = os.getenv("RAZORPAY_KEY_ID", "")
    RAZORPAY_KEY_SECRET: str = os.getenv("RAZORPAY_KEY_SECRET", "")
    RAZORPAY_WEBHOOK_SECRET: str = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
    RAZORPAY_API_BASE: str = os.getenv(
        "RAZORPAY_API_BASE", "https://api.razorpay.com/v1"
    ).rstrip("/")

    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL: str = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")


settings = Settings()