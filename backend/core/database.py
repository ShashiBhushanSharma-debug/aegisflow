from supabase import create_client, Client
from backend.config import settings

def get_supabase_client() -> Client:
    if not settings.SUPABASE_URL or not settings.SUPABASE_KEY:
        raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set in .env")
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

# Singleton instance
supabase: Client = get_supabase_client()