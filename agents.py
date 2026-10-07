"""Direct Groq LLM — no CrewAI. Reads GROQ_API_KEY from .env."""
import os
from groq import Groq
from dotenv import load_dotenv

load_dotenv()


def get_client():
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is missing in environment variables")
    return Groq(api_key=api_key)


def get_model_name():
    return os.getenv("GROQ_MODEL", "groq/qwen/qwen3.8-27b").replace("groq/", "")


def writer_agent():
    return None


def seo_agent():
    return None
