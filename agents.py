"""Direct Groq LLM — no CrewAI. Reads GROQ_API_KEY from .env."""
import os
from groq import Groq
from dotenv import load_dotenv

load_dotenv()


def get_client():
    """Returns a Groq client using GROQ_API_KEY from env."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is missing in environment variables")
    return Groq(api_key=api_key)


def get_model_name():
    """Returns model name without 'groq/' prefix.
    Uses llama-3.1-8b-instant: fast, non-reasoning, reliable JSON output.
    """
    return os.getenv("GROQ_MODEL", "llama-3.1-8b-instant").replace("groq/", "")


# Kept for backward compatibility with tasks.py imports.
# They return None because we don't use CrewAI agents anymore.
def writer_agent():
    return None


def seo_agent():
    return None
