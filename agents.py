"""Two LLM agents (Writer + SEO) running on Groq.
Draft deployment is deterministic (automation.py), not an agent."""
import os
from crewai import Agent, LLM
from dotenv import load_dotenv

load_dotenv()


def get_llm() -> LLM:
    return LLM(
        model=os.getenv("GROQ_MODEL", "groq/qwen/qwen3.8-27b"),
        api_key=os.getenv("GROQ_API_KEY"),
        temperature=0.7,
        max_tokens=4000,       # ← 6000 se 4000 (rate limit safe)
        max_retries=2,
    )


def writer_agent() -> Agent:
    return Agent(
        role="Content Generation Specialist",
        goal="Write 100% unique, human-like, well-structured guest articles that weave "
             "in the target keyword and URL naturally, without keyword stuffing.",
        backstory="Senior editor for niche publications; every piece is original, "
                  "useful and fits the host site's niche and rules.",
        llm=get_llm(), allow_delegation=False, verbose=False)


def seo_agent() -> Agent:
    return Agent(
        role="Yoast-style SEO Optimization Auditor",
        goal="Revise articles until they pass Yoast-style criteria: focus keyword in "
             "title, H2, first paragraph, meta description; readable short paragraphs "
             "and sentences.",
        backstory="Technical SEO who fixes exactly the failing checks and changes nothing else.",
        llm=get_llm(), allow_delegation=False, verbose=False)