import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    PROJECT_NAME: str = "AegisFlow Fintech AI Engine"
    API_V1_STR: str = "/api/v1"

    SUPABASE_URL:str = os.getenv("SUPABASE_URL","")
    SUPABASE_KEY: str = os.getenv("SUPABASE_KEY", "")

settings = Settings()
