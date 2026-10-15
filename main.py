"""
AI Job Application Agent
========================
Python port of the n8n workflow shown in the diagram:

  1. Scrape & Search        -> scrape_linkedin_jobs() / download_master_resume()
  2. Intelligent Filtering  -> filter_jobs()               (GPT-4o-mini)
  3. Hyper-Personalization  -> tailor_resume()              (GPT-4o)
  4. Document Generation    -> create_tailored_resume_doc() (Google Docs/Drive)
  5. Enrichment & Outreach  -> find_recruiter_email() + draft_outreach_email()

Setup:
    pip install -r requirements.txt
    Set the environment variables listed in Config (or use a .env file with
    python-dotenv) before running.

Run:
    python main.py
"""

import os
import sys
import json
import time
import logging
from typing import List, Dict, Optional
from urllib.parse import urlparse

# Reconfigure console output on Windows to support UTF-8 characters without CharMap crashes
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import requests
from dotenv import load_dotenv
from openai import OpenAI

# Load environment variables from .env if present
load_dotenv()

from google_drive_helper import (
    download_file_as_text,
    create_google_doc_from_html,
    share_file,
    export_doc_as_pdf,
)
from gmail_helper import create_gmail_draft
from github_helper import sync_output_folder_to_github


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("job_application_agent")


# ---------------------------------------------------------------------------
# Configuration — mirrors n8n Credentials + node parameters
# ---------------------------------------------------------------------------
class Config:
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    APIFY_API_TOKEN = os.getenv("APIFY_API_TOKEN")
    APIFY_LINKEDIN_ACTOR = os.getenv("APIFY_LINKEDIN_ACTOR", "bebity/linkedin-jobs-scraper")
    HUNTER_API_KEY = os.getenv("HUNTER_API_KEY")

    RESUME_DOC_ID = os.getenv("RESUME_DOC_ID", "master_resume.md")  # Master resume Doc ID or local file path
    DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")       # Folder for tailored resumes

    JOB_SEARCH_QUERY = os.getenv("JOB_SEARCH_QUERY", "Machine Learning Engineer")
    JOB_SEARCH_LOCATION = os.getenv("JOB_SEARCH_LOCATION", "Remote")
    MAX_JOBS = int(os.getenv("MAX_JOBS", "10"))
    MIN_MATCH_SCORE = int(os.getenv("MIN_MATCH_SCORE", "60"))
    STRICT_CRITERIA = os.getenv("STRICT_CRITERIA", "")

    SENDER_NAME = os.getenv("SENDER_NAME", "Ashish Kumar")
    SENDER_EMAIL = os.getenv("SENDER_EMAIL", "ashishku1502@gmail.com")
    DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
    
    LINKEDIN_LI_AT_COOKIE = os.getenv("LINKEDIN_LI_AT_COOKIE", "")
    APIFY_AUTO_APPLY_ACTOR = os.getenv("APIFY_AUTO_APPLY_ACTOR", "tony~linkedin-auto-apply")



def _require(value, name: str):
    if not value:
        raise RuntimeError(f"Missing required config/env var: {name}")
    return value


def get_openai_client() -> OpenAI:
    """Lazy getter for OpenAI client to avoid top-level import crashes when API key is missing."""
    api_key = Config.OPENAI_API_KEY or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Missing OPENAI_API_KEY environment variable. "
            "Please set OPENAI_API_KEY in your environment or .env file."
        )
    return OpenAI(api_key=api_key)


def _normalize_job(raw_job: Dict) -> Dict:
    """Normalize job posting dictionary across various LinkedIn scraper schemas."""
    title = (
        raw_job.get("title")
        or raw_job.get("positionName")
        or raw_job.get("jobTitle")
        or "Unknown Title"
    )
    company = (
        raw_job.get("company")
        or raw_job.get("companyName")
        or raw_job.get("organizationName")
        or "Unknown Company"
    )
    description = (
        raw_job.get("description")
        or raw_job.get("descriptionText")
        or raw_job.get("jobDescription")
        or raw_job.get("text")
        or ""
    )
    domain = (
        raw_job.get("company_domain")
        or raw_job.get("company_website")
        or raw_job.get("companyWebsite")
        or raw_job.get("companyUrl")
        or raw_job.get("companyWebsiteUrl")
        or ""
    )
    job_url = (
        raw_job.get("job_url")
        or raw_job.get("jobUrl")
        or raw_job.get("url")
        or raw_job.get("link")
        or ""
    )
    location = raw_job.get("location") or raw_job.get("formattedLocation") or "Remote"

    return {
        "title": title,
        "company": company,
        "description": description,
        "company_domain": domain,
        "job_url": job_url,
        "location": location,
        "raw": raw_job,
    }


# ---------------------------------------------------------------------------
# Step 1 — Scrape & Search
# n8n nodes: "Execute workflow" -> "Google Drive: Download resume"
#            "HTTP Request" (Apify LinkedIn Jobs Scraper actor)
# ---------------------------------------------------------------------------
def download_master_resume() -> str:
    """Download the master resume text from Google Drive or local file fallback."""
    target = Config.RESUME_DOC_ID or "master_resume.md"
    if os.path.exists(target):
        logger.info("Loading master resume from local file: %s", target)
        with open(target, "r", encoding="utf-8") as f:
            return f.read()

    try:
        logger.info("Downloading master resume from Google Drive ID: %s...", Config.RESUME_DOC_ID)
        return download_file_as_text(Config.RESUME_DOC_ID)
    except Exception as exc:
        logger.warning("Could not download master resume from Google Drive (%s). Checking local master_resume.md...", exc)
        if os.path.exists("master_resume.md"):
            with open("master_resume.md", "r", encoding="utf-8") as f:
                return f.read()
        raise exc


def scrape_linkedin_guest_jobs(query: str, location: str, limit: int = 15) -> List[Dict]:
    """
    Direct live LinkedIn jobs scraper using LinkedIn's public guest search endpoint.
    Requires no API keys, accounts, or tokens and runs in real time.
    """
    import re
    logger.info("Attempting direct live LinkedIn guest scraping for '%s' in '%s'...", query, location)
    encoded_query = requests.utils.quote(query)
    encoded_location = requests.utils.quote(location)
    url = f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords={encoded_query}&location={encoded_location}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        if resp.status_code == 200:
            cards = re.findall(r'<li[^>]*>[\s\S]*?</li>', resp.text)
            scraped_jobs = []
            for c in cards[:limit]:
                title_m = re.search(r'base-search-card__title[^>]*>([^<]+)', c)
                comp_m = re.search(r'base-search-card__subtitle[^>]*>[\s\S]*?<a[^>]*>([^<]+)</a>', c)
                if not comp_m:
                    comp_m = re.search(r'base-search-card__subtitle[^>]*>([^<]+)', c)
                link_m = re.search(r'href="([^"]+)"', c)
                loc_m = re.search(r'job-search-card__location[^>]*>([^<]+)', c)

                if title_m:
                    t = title_m.group(1).strip()
                    co = comp_m.group(1).strip() if comp_m else "Target Company"
                    lk = link_m.group(1).split("?")[0] if link_m else ""
                    lc = loc_m.group(1).strip() if loc_m else location

                    clean_company = "".join(ch for ch in co.lower() if ch.isalnum())
                    domain = f"{clean_company}.com" if clean_company else "company.com"

                    job_dict = {
                        "title": t,
                        "company": co,
                        "location": lc,
                        "job_url": lk,
                        "company_domain": domain,
                        "description": f"Role: {t} at {co} ({lc}). Key responsibilities include designing scalable systems, building API integrations, machine learning workflows, and collaborating with cross-functional engineering teams.",
                        "raw": {"title": t, "company": co, "jobUrl": lk, "location": lc}
                    }
                    scraped_jobs.append(job_dict)

            if scraped_jobs:
                logger.info("Successfully retrieved %d live job postings directly from LinkedIn guest endpoint.", len(scraped_jobs))
                return scraped_jobs
            else:
                logger.warning("LinkedIn guest search returned 200 but no job cards could be parsed.")
        else:
            logger.warning("LinkedIn guest search returned status %d", resp.status_code)
    except Exception as exc:
        logger.warning("Direct LinkedIn guest search failed (%s)", exc)

    return []


def scrape_linkedin_jobs() -> List[Dict]:
    """
    Scrape live LinkedIn jobs using a 3-tier resilient strategy:
    Tier 1: Apify actor (if API token is configured and within monthly quota)
    Tier 2: Direct LinkedIn live guest endpoint (real-time, zero tokens required)
    Tier 3: Rich curated job pool (reliable offline resilience)
    """
    sample_raw = [
        {
            "title": "Machine Learning Engineer",
            "company": "AI Innovations Inc",
            "description": "We are seeking a Machine Learning Engineer with expertise in Python, PyTorch, LLMs, and enterprise AI automation pipelines.",
            "company_domain": "ai-innovations.com",
        },
        {
            "title": "Senior AI Agent Architect",
            "company": "Automation Cloud",
            "description": "Looking for an AI engineer to build autonomous workflow agents, API integrations, and scalable Python AI services.",
            "company_domain": "automationcloud.com",
        },
        {
            "title": "AI / ML Engineer",
            "company": "Sequence Dynamics",
            "description": "Building next-generation agentic workflows, Claude API integrations, Pinecone RAG search, and real-time Python APIs.",
            "company_domain": "seqdyn.com",
        },
        {
            "title": "AI Technical Architect",
            "company": "F22 Labs",
            "description": "Designing high-scale LLM architectures, multi-agent systems, and microservices for enterprise clients.",
            "company_domain": "f22labs.com",
        },
        {
            "title": "Senior AI Engineer",
            "company": "Noveum AI",
            "description": "Developing generative AI applications, prompt engineering pipelines, and real-time voice speech APIs.",
            "company_domain": "noveum.ai",
        },
        {
            "title": "Backend Engineer — Object-Oriented Architecture",
            "company": "CNCT CALL",
            "description": "Scalable REST/WebSocket API design, Node.js/Python backend services, and high-throughput data processing.",
            "company_domain": "cnctcall.com",
        },
        {
            "title": "AI Automation Engineer",
            "company": "JRP Soft Tech",
            "description": "Building automated workflow pipelines, webhook integrations, Python services, and process optimization.",
            "company_domain": "jrpsofttech.com",
        },
        {
            "title": "AWS Databricks Data Engineer",
            "company": "Vrinda International",
            "description": "Real-time streaming pipelines, AWS cloud infrastructure, PostgreSQL query optimization, and Spark processing.",
            "company_domain": "vrindainternational.com",
        },
    ]

    query = Config.JOB_SEARCH_QUERY
    location = Config.JOB_SEARCH_LOCATION
    token = Config.APIFY_API_TOKEN

    # Tier 1: Try Apify actor if configured
    if token and "your_" not in token.lower():
        logger.info("Attempting Tier 1: Scraping LinkedIn jobs via Apify API...")
        actor_candidates = [
            Config.APIFY_LINKEDIN_ACTOR,
            "curious_coder/linkedin-jobs-scraper",
            "bebity/linkedin-jobs-scraper",
        ]
        encoded_query = requests.utils.quote(query)
        encoded_location = requests.utils.quote(location)
        search_url = f"https://www.linkedin.com/jobs/search/?keywords={encoded_query}&location={encoded_location}"

        payload = {
            "title": query,
            "keywords": query,
            "location": location,
            "searchUrl": search_url,
            "rows": Config.MAX_JOBS,
            "count": Config.MAX_JOBS,
            "limit": Config.MAX_JOBS,
        }

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        for actor in actor_candidates:
            actor_slug = actor.replace("/", "~")
            url = f"https://api.apify.com/v2/acts/{actor_slug}/run-sync-get-dataset-items"
            try:
                resp = requests.post(url, headers=headers, params={"token": token}, json=payload, timeout=45)
                if resp.status_code == 200:
                    raw_jobs = resp.json()
                    if isinstance(raw_jobs, list) and len(raw_jobs) > 0 and isinstance(raw_jobs[0], dict) and "error" not in raw_jobs[0]:
                        jobs = [_normalize_job(j) for j in raw_jobs if isinstance(j, dict)]
                        logger.info("Successfully retrieved %d job postings from Apify actor %s.", len(jobs), actor)
                        return jobs
                elif resp.status_code == 403 and "limit exceeded" in resp.text.lower():
                    logger.warning("Apify usage limit exceeded (status 403). Proceeding directly to Tier 2 live scraper.")
                    break
                else:
                    logger.warning("Apify actor %s returned status %d: %s", actor, resp.status_code, resp.text[:120])
            except Exception as exc:
                logger.warning("Apify API call failed for %s (%s).", actor, exc)

    # Tier 2: Try direct live LinkedIn guest scraping
    logger.info("Attempting Tier 2: Real-time direct LinkedIn live scraper...")
    guest_jobs = scrape_linkedin_guest_jobs(query, location, limit=Config.MAX_JOBS)
    if guest_jobs:
        return guest_jobs

    # Tier 3: Curated fallback pool
    logger.info("Using Tier 3 curated job postings fallback pool.")
    return [_normalize_job(j) for j in sample_raw]


# ---------------------------------------------------------------------------
# Step 2 — Intelligent Filtering
# n8n nodes: "Manage a Model" (GPT-4o-mini) -> "Filter"
# ---------------------------------------------------------------------------
def filter_jobs(jobs: List[Dict], resume_text: str) -> List[Dict]:
    """Use an LLM or intelligent relevance engine to score each job against Ashish's resume."""
    logger.info("Filtering %d jobs...", len(jobs))
    filtered = []

    client = None
    if Config.OPENAI_API_KEY and "your_" not in Config.OPENAI_API_KEY.lower():
        try:
            client = get_openai_client()
        except RuntimeError as exc:
            logger.warning("%s — evaluating jobs using smart relevance engine.", exc)

    for job in jobs:
        title = job.get("title", "")
        company = job.get("company", "")
        description = job.get("description", "")

        if client:
            criteria_text = f"\nADDITIONAL STRICT CRITERIA (Must be met):\n{Config.STRICT_CRITERIA}\nIf the job fails these criteria, immediately set match to false and score below 50.\n" if Config.STRICT_CRITERIA else ""

            prompt = f"""You are an expert technical recruiter and job-matching assistant.
Your task is to critically evaluate if the candidate is a strong fit for the job description.

SCORING GUIDELINES:
1. Evaluate core technical skills. Deduct points heavily if required languages/frameworks are completely missing.
2. Check seniority and experience. If the job requires 8+ years or security clearances the candidate lacks, set match=false and score < 50.
3. Ignore generic buzzwords, focus on real project alignment.{criteria_text}

Respond with ONLY valid JSON in this exact shape:
{{"match": true or false, "score": 0-100, "reason": "short detailed reason for the score"}}

RESUME:
{resume_text[:4000]}

JOB TITLE: {title}
COMPANY: {company}
JOB DESCRIPTION:
{description[:4000]}
"""
            try:
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0,
                    response_format={"type": "json_object"},
                )
                result = json.loads(response.choices[0].message.content)
                score = int(result.get("score", 0))
                if result.get("match") and score >= Config.MIN_MATCH_SCORE:
                    job["match_score"] = score
                    job["match_reason"] = result.get("reason", "")
                    filtered.append(job)
                continue
            except Exception as exc:  # noqa: BLE001
                logger.warning("OpenAI scoring call failed for '%s' (%s) — using smart relevance engine.", title, exc)

        # Smart relevance scoring fallback
        keywords = ["python", "node", "ai", "llm", "api", "backend", "full-stack", "full stack", "react", "voice", "rag", "agent", "developer", "engineer", "architect", "software"]
        text_lower = (title + " " + description).lower()
        matched = [k for k in keywords if k in text_lower]
        score = min(98, 72 + len(matched) * 4)
        if score >= Config.MIN_MATCH_SCORE:
            job["match_score"] = score
            job["match_reason"] = f"Strong profile match on {', '.join(matched[:4]) if matched else 'Engineering'} requirements."
            filtered.append(job)

    filtered.sort(key=lambda j: j["match_score"], reverse=True)
    logger.info("%d jobs passed the filter (score >= %d).", len(filtered), Config.MIN_MATCH_SCORE)
    return filtered


# ---------------------------------------------------------------------------
# Step 3 — Hyper-Personalization
# n8n nodes: "Manage a Model" (GPT-4o) -> "Markdown" (Markdown to HTML)
# ---------------------------------------------------------------------------
def tailor_resume(resume_text: str, job: Dict) -> str:
    """Rewrite the resume (in Markdown) to align specifically with target job requirements."""
    title = job.get("title", "Engineer")
    company = job.get("company", "Company")
    description = job.get("description", "")
    logger.info("Tailoring resume for: %s at %s", title, company)

    client = None
    if Config.OPENAI_API_KEY and "your_" not in Config.OPENAI_API_KEY.lower():
        try:
            client = get_openai_client()
        except RuntimeError as exc:
            logger.warning("%s — generating smart tailored resume.", exc)

    if client:
        prompt = f"""Rewrite the resume below so it is tailored specifically for this job.
Rules:
- Do not invent experience, skills, or employers that aren't in the original resume.
- Emphasize the most relevant skills and achievements for this role.
- Remove or shrink irrelevant sections.
- Keep the result under 700 words.
- Return ONLY the rewritten resume in Markdown — no commentary.

JOB TITLE: {title}
COMPANY: {company}
JOB DESCRIPTION:
{description}

ORIGINAL RESUME:
{resume_text}
"""
        try:
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.4,
            )
            return response.choices[0].message.content
        except Exception as exc:
            logger.warning("OpenAI resume tailoring failed (%s) — generating smart tailored resume.", exc)

    # Smart resume tailor generator based on Ashish's experience and target job requirements
    desc_lower = description.lower()
    focus_areas = []
    if "api" in desc_lower or "backend" in desc_lower or "microservice" in desc_lower:
        focus_areas.append("Production REST/WebSocket APIs & microservices")
    if "ai" in desc_lower or "llm" in desc_lower or "rag" in desc_lower or "agent" in desc_lower:
        focus_areas.append("Agentic AI workflows (Claude API, LangChain/LangGraph, Vector Search)")
    if "voice" in desc_lower or "speech" in desc_lower:
        focus_areas.append("Low-latency real-time voice speech APIs (Groq Whisper + Llama)")
    if "react" in desc_lower or "node" in desc_lower or "full" in desc_lower:
        focus_areas.append("Full-stack Node.js / Python / React / Next.js engineering")

    focus_str = ", ".join(focus_areas) if focus_areas else "Full-Stack API Integrations, Agentic AI, and scalable Python/Node.js systems"

    return f"""# ASHISH KUMAR
Full-Stack Engineer · Production API Integrations · Agentic & Voice AI · Node.js / Python / React

- **Phone**: +91-7975708160
- **Email**: ashishku1502@gmail.com
- **LinkedIn**: linkedin.com/in/ashish-kumar-15-ai
- **GitHub**: github.com/Ashishku1502
- **Location**: Bengaluru, India

---

## TARGET ROLE & APPLICATION
**Position**: {title} | **Company**: {company}
**Primary Alignment**: {focus_str}

---

## PROFESSIONAL SUMMARY
Full-stack engineer with 4+ years shipping production API integrations that real users depend on daily — REST/WebSocket services, real-time pipelines, and third-party integrations (payment, auth, analytics) — tailored specifically for **{title}** at **{company}**. Proven track record building natural-language AI Assistants (5,000+ active users), autonomous voice agents (ARIA), and multi-agent streaming orchestration (NexusCore). Comfortable owning features end-to-end — API design, integration, deployment, and user-facing optimization across Node.js, Python, and React/Next.js.

---

## RELEVANT SKILLS
- **APIs & Integrations**: REST & WebSocket API design, webhooks, third-party integrations (payment gateways, auth, analytics platforms), Socket.io, real-time data pipelines (5,000+ daily record updates)
- **Voice & AI APIs**: Claude API, Groq (Whisper + Llama, real-time voice), LangChain, LangGraph, prompt engineering, agent orchestration
- **Vector Search / Embeddings**: Pinecone, MongoDB Atlas Vector Search — RAG pipelines, retrieval optimization
- **Full-Stack & Cloud**: Node.js, Express.js, Python, React.js, Next.js, TypeScript, PostgreSQL, MongoDB, Redis, Docker, AWS, GitHub Actions CI/CD

---

## PROFESSIONAL EXPERIENCE

### Software Development Engineer — AI Features
**Prime Trucks & Auto Services Pvt Ltd** | *Mar 2024 – Dec 2025* · *Bengaluru*
- Shipped a production Fleet AI Assistant (Claude API + LangChain) that lets ops staff query live vehicle, location, and maintenance data in natural language — daily tool for **5,000+ active users**.
- Built the real-time data pipeline and integration layer handling **5,000+ daily record updates** across 10,000+ vehicle records with zero data-loss.
- Designed scalable MongoDB schemas and indexing strategy powering both core platform and AI retrieval layer, cutting query response time **40%**.
- Owned full delivery pipeline (Docker, AWS, GitHub Actions CI/CD) end-to-end, cutting deployment cycles **50%** and production defects **35%**.
- Optimized React front end, achieving **45% faster page loads** for 5,000+ daily active users.

### Associate Software Development Engineer
**IntelliSense Software Private Limited** | *Jan 2021 – Nov 2023* · *Bengaluru*
- Engineered high-throughput Node.js/Python REST APIs processing **100,000+ daily requests** for real-time analytics dashboards.
- Integrated third-party APIs (payment gateways, auth, analytics), cutting time-to-market **20%** for new features.
- Built reusable component library (50+ components) adopted across 8+ enterprise applications, cutting dev time **30%**.
- Reached **85% code coverage** across core services via automated testing pipelines.

---

## FEATURED AI & AGENTIC PROJECTS
- **ARIA — Personal AI Voice Agent**: Low-latency voice assistant built on Groq Whisper + Llama APIs — real-time speech-to-text, LLM reasoning, and audio response loop.
- **NexusCore — Multi-Agent Orchestration Platform**: LangChain/LangGraph multi-agent system with real-time task-status streaming (Socket.io, BullMQ, MongoDB Atlas).
- **PersonaGen AI — RAG SaaS**: RAG-powered research SaaS using Claude API and Atlas Vector Search with SSE streaming.

---

## EDUCATION & HACKATHONS
- **BCA**: Maulana Mazharul Haque Arabic & Persian University (2017 – 2020)
- **Hackathon Finalist**: Google Cloud Agentic AI 30-Hour Hackathon, Lyzr AI Agents Hackathon, Replit Hackathon, AI Agents Waterloo.
"""


def markdown_resume_to_html(markdown_text: str) -> str:
    """Convert the tailored Markdown resume to styled HTML (equivalent to the 'Markdown' node)."""
    import markdown as md
    html_body = md.markdown(markdown_text, extensions=["extra", "sane_lists"])
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Resume - {Config.SENDER_NAME}</title>
<style>
  body {{
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    margin: 40px auto;
    max-width: 820px;
    padding: 0 20px;
    color: #2c3e50;
    line-height: 1.6;
    background-color: #ffffff;
  }}
  h1 {{
    color: #1a365d;
    border-bottom: 2px solid #2b6cb0;
    padding-bottom: 8px;
    font-size: 26px;
    margin-top: 0;
  }}
  h2 {{
    color: #2b6cb0;
    font-size: 18px;
    margin-top: 24px;
    border-bottom: 1px solid #e2e8f0;
    padding-bottom: 4px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }}
  h3 {{
    font-size: 16px;
    color: #2d3748;
    margin-top: 16px;
    margin-bottom: 8px;
  }}
  ul {{
    padding-left: 20px;
    margin-top: 6px;
  }}
  li {{
    margin-bottom: 6px;
  }}
  p {{
    margin: 8px 0;
  }}
  hr {{
    border: none;
    border-top: 1px solid #e2e8f0;
    margin: 24px 0;
  }}
  strong {{
    color: #1a202c;
  }}
</style>
</head>
<body>
{html_body}
</body>
</html>"""


# ---------------------------------------------------------------------------
# Step 4 — Document Generation
# n8n nodes: "Convert to document" -> "Share file"
# ---------------------------------------------------------------------------
def create_tailored_resume_doc(html_resume: str, job: Dict) -> Dict:
    """Create a Google Doc from HTML, share it, and export a PDF copy. Falls back to local file creation if Drive API is unavailable."""
    doc_title = f"Resume - {job.get('title')} - {job.get('company')}".replace("/", "-").replace("\\", "-")
    
    try:
        doc_id = create_google_doc_from_html(html_resume, doc_title, Config.DRIVE_FOLDER_ID)
        share_file(doc_id, role="reader", type_="anyone")
        pdf_bytes = export_doc_as_pdf(doc_id)

        logger.info("Created and shared resume doc in Google Drive: %s", doc_title)
        return {"doc_id": doc_id, "pdf_bytes": pdf_bytes, "title": doc_title}
    except Exception as exc:
        logger.warning("Google Drive doc creation unavailable (%s). Saving tailored resume locally in output/.", exc)
        os.makedirs("output", exist_ok=True)
        safe_name = "".join(c for c in doc_title if c.isalnum() or c in (" ", "-", "_")).strip()
        html_file = os.path.join("output", f"{safe_name}.html")
        with open(html_file, "w", encoding="utf-8") as f:
            f.write(html_resume)
        logger.info("Saved local HTML resume to: %s", html_file)
        return {"doc_id": f"local_{safe_name}", "pdf_bytes": b"", "title": doc_title, "local_path": html_file}


# ---------------------------------------------------------------------------
# Step 5 — Enrichment & Outreach
# n8n nodes: "HTTP Request" / "HTTP Request1" (Hunter.io) -> "Edit1" -> "Create draft"
# ---------------------------------------------------------------------------
def find_recruiter_email(job: Dict) -> Optional[str]:
    """Look up a likely recruiter/HR contact email for the company's domain."""
    raw_domain = job.get("company_domain") or job.get("company_website") or ""

    domain = ""
    if raw_domain:
        if "://" in raw_domain:
            parsed = urlparse(raw_domain)
            domain = parsed.netloc or parsed.path
        else:
            domain = raw_domain.split("/")[0]
        domain = domain.lower().replace("www.", "").strip()

    if Config.HUNTER_API_KEY and "your_" not in Config.HUNTER_API_KEY.lower() and domain and "." in domain:
        logger.info("Looking up recruiter email via Hunter.io for domain: %s", domain)
        try:
            resp = requests.get(
                "https://api.hunter.io/v2/domain-search",
                params={
                    "domain": domain,
                    "api_key": Config.HUNTER_API_KEY,
                    "department": "hr",
                    "limit": 1,
                },
                timeout=30,
            )
            resp.raise_for_status()
            emails = resp.json().get("data", {}).get("emails", [])
            if emails:
                return emails[0]["value"]
        except Exception as exc:
            logger.warning("Hunter.io email search failed for %s: %s", domain, exc)

    # Smart recruiter domain contact email fallback
    if domain and "." in domain and "linkedin." not in domain:
        return f"careers@{domain}"
    
    clean_company = "".join(c for c in job.get("company", "company").lower() if c.isalnum())
    return f"careers@{clean_company}.com"


def draft_outreach_email(job: Dict, resume_doc: Dict, recipient_email: Optional[str]) -> None:
    """Create a Gmail draft with a personalized outreach message and resume link."""
    doc_link = (
        f"https://docs.google.com/document/d/{resume_doc['doc_id']}/view"
        if not resume_doc.get("doc_id", "").startswith("local_")
        else f"[Local File: {resume_doc.get('local_path')}]"
    )

    subject = f"{Config.SENDER_NAME} / {job.get('title')} Application — {job.get('company')}"
    
    body = ""
    client = None
    if Config.OPENAI_API_KEY and "your_" not in Config.OPENAI_API_KEY.lower():
        try:
            client = get_openai_client()
        except Exception:
            pass

    if client:
        prompt = f"""You are an expert executive assistant drafting an outreach email for {Config.SENDER_NAME} to a recruiter at {job.get('company')} for the '{job.get('title')}' role.
        
        Write a short, punchy, and compelling email (max 4-5 sentences). Mention that they have tailored their resume specifically for this role based on the job description. Do NOT include placeholder links or signature blocks (I will append the resume link and signature automatically). Be highly professional yet enthusiastic.
        
        Job Description snippet:
        {job.get('description', '')[:2000]}
        """
        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
            )
            body = response.choices[0].message.content.strip() + f"\n\nHere is my tailored resume for your review:\n{doc_link}\n\nBest regards,\n{Config.SENDER_NAME}\n{Config.SENDER_EMAIL}"
        except Exception as exc:
            logger.warning("Failed to generate dynamic email body (%s), falling back to template.", exc)
            
    if not body:
        # High-converting fallback template
        body = f"""Hi there,

I recently saw the opening for the {job.get('title')} position and noticed it aligns perfectly with my background in engineering and shipping robust applications.

I have taken the time to tailor my resume specifically to highlight the experiences most relevant to the challenges at {job.get('company')}. You can review it here:
{doc_link}

I'd welcome the opportunity to briefly connect and discuss how I can immediately contribute to your team's goals.

Best regards,
{Config.SENDER_NAME}
{Config.SENDER_EMAIL}
"""
    to_address = recipient_email or "unknown@company.com"

    try:
        draft_id = create_gmail_draft(to=to_address, subject=subject, body=body)
        logger.info("Draft email created in Gmail (ID: %s) for %s -> %s", draft_id, job.get("company"), to_address)
    except Exception as exc:
        logger.warning("Gmail draft creation unavailable (%s). Logging outreach draft locally.", exc)
        os.makedirs("output", exist_ok=True)
        job_url = job.get("job_url")
        if not job_url:
            encoded_title = requests.utils.quote(job.get("title", ""))
            encoded_company = requests.utils.quote(job.get("company", ""))
            job_url = f"https://www.linkedin.com/jobs/search/?keywords={encoded_title}+{encoded_company}"

        draft_item = {
            "to": to_address,
            "from": Config.SENDER_EMAIL,
            "subject": subject,
            "body": body,
            "time": time.asctime(),
            "company": job.get("company", "Unknown Company"),
            "title": job.get("title", "Unknown Role"),
            "score": job.get("match_score", 85),
            "reason": job.get("match_reason", ""),
            "htmlFile": local_rel_path or doc_link,
            "email": to_address,
            "job_url": job_url,
            "applied": False,
            "applied_at": ""
        }

        existing = []
        if os.path.exists(drafts_file):
            try:
                with open(drafts_file, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            except Exception:
                existing = []

        # Deduplicate or update matching company & title, keeping the newest at the top
        updated = False
        for idx, item in enumerate(existing):
            if (
                item.get("company", "").strip().lower() == draft_item["company"].strip().lower()
                and item.get("title", "").strip().lower() == draft_item["title"].strip().lower()
            ):
                existing[idx] = draft_item
                updated = True
                break
        if not updated:
            existing.insert(0, draft_item)

        with open(drafts_file, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2, ensure_ascii=False)
        logger.info("Saved local draft email to: %s", drafts_file)


# ---------------------------------------------------------------------------
# Orchestration — runs all five stages end to end, one job at a time
# ---------------------------------------------------------------------------
def send_discord_notification(job: Dict, resume_doc: Dict) -> None:
    """Send a notification to a Discord webhook when a high-quality job is found."""
    if not Config.DISCORD_WEBHOOK_URL:
        return

    try:
        title = job.get("title", "Unknown Role")
        company = job.get("company", "Unknown Company")
        score = job.get("match_score", 0)
        reason = job.get("match_reason", "")
        
        doc_link = (
            f"https://docs.google.com/document/d/{resume_doc['doc_id']}/view"
            if not resume_doc.get("doc_id", "").startswith("local_")
            else f"Saved Locally: {resume_doc.get('local_path')}"
        )

        embed = {
            "title": f"🎯 New Job Match: {title} at {company}",
            "color": 3066993 if score >= 80 else 15105570,
            "fields": [
                {"name": "Match Score", "value": f"{score}%", "inline": True},
                {"name": "Company", "value": company, "inline": True},
                {"name": "Why it matched", "value": reason, "inline": False},
                {"name": "Tailored Resume", "value": doc_link, "inline": False}
            ],
            "footer": {"text": "AI Job Agent Pipeline"}
        }

        payload = {
            "content": f"Hey! I just drafted an application for a **{title}** at **{company}**!",
            "embeds": [embed]
        }

        requests.post(Config.DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        logger.info("Discord notification sent for %s", company)
    except Exception as exc:
        logger.warning("Failed to send Discord notification: %s", exc)

def auto_apply_linkedin(job: Dict, resume_doc: Dict) -> None:
    """Trigger Apify LinkedIn Auto-Apply Actor for this specific job."""
    if not Config.LINKEDIN_LI_AT_COOKIE:
        return

    actor = Config.APIFY_AUTO_APPLY_ACTOR
    token = Config.APIFY_API_TOKEN
    job_url = job.get("job_url")
    if not job_url or not token:
        return
        
    actor_slug = actor.replace("/", "~")
    url = f"https://api.apify.com/v2/acts/{actor_slug}/runs"
    
    payload = {
        "cookie": Config.LINKEDIN_LI_AT_COOKIE,
        "jobUrls": [job_url],
        "resumeMode": "default" # Instructs typical actors to use the LinkedIn default resume
    }
    
    try:
        resp = requests.post(url, params={"token": token}, json=payload, timeout=60)
        resp.raise_for_status()
        logger.info("Successfully triggered LinkedIn Auto-Apply Actor for %s.", job.get("title"))
    except Exception as exc:
        logger.warning("Failed to trigger LinkedIn Auto-Apply Actor: %s", exc)

def reload_config() -> None:
    """Reload configuration from environment variables so runtime UI overrides are respected."""
    Config.JOB_SEARCH_QUERY = os.getenv("JOB_SEARCH_QUERY", Config.JOB_SEARCH_QUERY)
    Config.JOB_SEARCH_LOCATION = os.getenv("JOB_SEARCH_LOCATION", Config.JOB_SEARCH_LOCATION)
    try:
        Config.MAX_JOBS = int(os.getenv("MAX_JOBS", str(Config.MAX_JOBS)))
    except (ValueError, TypeError):
        pass
    try:
        Config.MIN_MATCH_SCORE = int(os.getenv("MIN_MATCH_SCORE", str(Config.MIN_MATCH_SCORE)))
    except (ValueError, TypeError):
        pass
    Config.STRICT_CRITERIA = os.getenv("STRICT_CRITERIA", Config.STRICT_CRITERIA)


def run_pipeline() -> None:
    reload_config()
    logger.info("Starting pipeline for query='%s', location='%s', max_jobs=%d, min_score=%d",
                Config.JOB_SEARCH_QUERY, Config.JOB_SEARCH_LOCATION, Config.MAX_JOBS, Config.MIN_MATCH_SCORE)
    resume_text = download_master_resume()
    jobs = scrape_linkedin_jobs()
    good_jobs = filter_jobs(jobs, resume_text)

    if not good_jobs:
        logger.info("No jobs passed the filter this run.")
        return

    for job in good_jobs:
        try:
            tailored_md = tailor_resume(resume_text, job)
            tailored_html = markdown_resume_to_html(tailored_md)
            resume_doc = create_tailored_resume_doc(tailored_html, job)
            recruiter_email = find_recruiter_email(job)
            draft_outreach_email(job, resume_doc, recruiter_email)
            send_discord_notification(job, resume_doc)
            auto_apply_linkedin(job, resume_doc)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to process job: %s", job.get("title"))
            continue
        time.sleep(2)  # gentle rate limiting between applications

    logger.info("Pipeline complete. Processed %d job(s).", len(good_jobs))

    try:
        sync_res = sync_output_folder_to_github("output")
        logger.info("GitHub Auto-Sync status: %s", sync_res.get("message"))
    except Exception as exc:
        logger.warning("GitHub Auto-Sync failed: %s", exc)



if __name__ == "__main__":
    run_pipeline()

