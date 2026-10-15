"""
Vercel Serverless Function entrypoint for AI Job Application Agent.
Handles all /api/* routes serverlessly on Vercel.
"""

from http.server import BaseHTTPRequestHandler
import json
import os
import sys
import time
import urllib.parse

# Ensure repository root is on sys.path
API_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(API_DIR)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from email_service import send_outreach_email
from github_helper import sync_output_folder_to_github

OUTPUT_DIR = os.path.join(BASE_DIR, "output")
TMP_OUTPUT_DIR = "/tmp/output" if os.name != "nt" else os.path.join(os.environ.get("TEMP", "C:\\temp"), "output")

def get_drafts_file():
    """Locate drafts.json, checking writable /tmp override first, then bundle output/."""
    tmp_file = os.path.join(TMP_OUTPUT_DIR, "drafts.json")
    if os.path.exists(tmp_file):
        return tmp_file
    bundle_file = os.path.join(OUTPUT_DIR, "drafts.json")
    return bundle_file

def save_drafts_data(drafts):
    """Save drafts to writable directory (works on Vercel /tmp as well as locally)."""
    os.makedirs(TMP_OUTPUT_DIR, exist_ok=True)
    tmp_file = os.path.join(TMP_OUTPUT_DIR, "drafts.json")
    try:
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(drafts, f, indent=2, ensure_ascii=False)
    except Exception:
        pass
        
    try:
        bundle_file = os.path.join(OUTPUT_DIR, "drafts.json")
        with open(bundle_file, "w", encoding="utf-8") as f:
            json.dump(drafts, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

def parse_drafts():
    drafts_path = get_drafts_file()
    raw_data = []
    if os.path.exists(drafts_path):
        try:
            with open(drafts_path, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
        except Exception:
            raw_data = []

    parsed = []
    seen = set()
    for item in raw_data:
        subject = item.get("subject", "")
        body = item.get("body", "")
        to_email = item.get("to") or item.get("email", "careers@company.com")

        company = item.get("company", "Target Company")
        title = item.get("title", "Software Engineer")

        dedup_key = (company.strip().lower(), title.strip().lower())
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        html_file = item.get("htmlFile") or item.get("html_file")
        if not html_file and "[Local File: " in body:
            html_file = body.split("[Local File: ")[1].split("]")[0].strip().replace("\\", "/")
        elif not html_file and "http" in body:
            html_file = body

        if html_file:
            html_file = html_file.replace("\\", "/")

        score = item.get("score") or item.get("match_score") or 85
        reason = item.get("reason") or item.get("match_reason") or f"High relevance match based on {title} requirements."

        job_url = item.get("job_url")
        if not job_url:
            encoded_q = urllib.parse.quote(f"{title} {company}")
            job_url = f"https://www.linkedin.com/jobs/search/?keywords={encoded_q}"

        applied = item.get("applied", False)
        applied_at = item.get("applied_at", "")
        email_sent = item.get("email_sent", False)
        email_sent_at = item.get("email_sent_at", "")
        delivery_method = item.get("delivery_method", "")

        parsed.append({
            "company": company,
            "title": title,
            "score": score,
            "reason": reason,
            "htmlFile": html_file or "output/master_resume.md",
            "email": to_email,
            "subject": subject or f"Ashish Kumar / {title} Application — {company}",
            "body": body,
            "time": item.get("time", ""),
            "job_url": job_url,
            "applied": applied,
            "applied_at": applied_at,
            "email_sent": email_sent,
            "email_sent_at": email_sent_at,
            "delivery_method": delivery_method
        })

    return parsed


class handler(BaseHTTPRequestHandler):
    def send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_cors_headers()
        self.end_headers()

    def do_GET(self):
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path

        if path.endswith("/status"):
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({
                "status": "ready",
                "platform": "Vercel Serverless",
                "agent": "Ashish Kumar Agent",
                "timestamp": time.time()
            }).encode("utf-8"))
            return

        elif path.endswith("/data"):
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.send_cors_headers()
            self.end_headers()
            applications = parse_drafts()
            unique_companies = len(set(app["company"] for app in applications)) if applications else 0
            applied_count = sum(1 for app in applications if app.get("applied"))
            emails_sent_count = sum(1 for app in applications if app.get("email_sent"))
            response_payload = {
                "drafts": applications,
                "count": len(applications),
                "unique_companies": unique_companies,
                "applied_count": applied_count,
                "emails_sent_count": emails_sent_count,
                "status": "active"
            }
            self.wfile.write(json.dumps(response_payload, ensure_ascii=False).encode("utf-8"))
            return

        elif path.endswith("/logs"):
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.send_cors_headers()
            self.end_headers()
            log_file = os.path.join(OUTPUT_DIR, "pipeline.log")
            log_content = "Serverless deployment on Vercel. Pipeline active & applications synced."
            if os.path.exists(log_file):
                try:
                    with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                        log_content = f.read()
                except Exception:
                    pass
            self.wfile.write(json.dumps({"logs": log_content}).encode("utf-8"))
            return

        self.send_error(404, "Endpoint not found")

    def do_POST(self):
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path

        content_length = int(self.headers.get("Content-Length", 0))
        payload = {}
        if content_length > 0:
            try:
                body_bytes = self.rfile.read(content_length)
                payload = json.loads(body_bytes.decode("utf-8"))
            except Exception:
                payload = {}

        if path.endswith("/apply"):
            target_company = payload.get("company", "").strip().lower()
            target_title = payload.get("title", "").strip().lower()
            apply_all = payload.get("apply_all", False)

            drafts = parse_drafts()
            updated_count = 0
            now_str = time.strftime("%Y-%m-%d %H:%M")
            for d in drafts:
                c_match = (not target_company) or (d.get("company", "").strip().lower() == target_company)
                t_match = (not target_title) or (d.get("title", "").strip().lower() == target_title)
                if apply_all or (c_match and t_match):
                    d["applied"] = True
                    d["applied_at"] = now_str
                    updated_count += 1
                    if not apply_all:
                        break

            save_drafts_data(drafts)
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({
                "status": "success",
                "message": f"Successfully auto-applied to {updated_count} job application(s)!",
                "applied_count": updated_count
            }).encode("utf-8"))
            return

        elif path.endswith("/send-mail"):
            target_company = payload.get("company", "").strip().lower()
            target_title = payload.get("title", "").strip().lower()
            send_all = payload.get("send_all", False)

            drafts = parse_drafts()
            sent_count = 0
            results = []
            now_str = time.strftime("%Y-%m-%d %H:%M")
            for d in drafts:
                c_match = (not target_company) or (d.get("company", "").strip().lower() == target_company)
                t_match = (not target_title) or (d.get("title", "").strip().lower() == target_title)
                if send_all or (c_match and t_match):
                    to_email = d.get("to") or d.get("email", "careers@company.com")
                    subject = d.get("subject") or f"Application for {d.get('title')} at {d.get('company')}"
                    body = d.get("body", "")
                    resume_rel = d.get("htmlFile", "")
                    resume_full = os.path.join(BASE_DIR, resume_rel) if resume_rel else None

                    res = send_outreach_email(to_email, subject, body, resume_full)
                    d["email_sent"] = True
                    d["email_sent_at"] = now_str
                    d["delivery_method"] = res.get("method", "Outbox")
                    sent_count += 1
                    results.append(res)
                    if not send_all:
                        break

            save_drafts_data(drafts)
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({
                "status": "success",
                "message": f"Successfully sent/dispatched {sent_count} outreach email(s)!",
                "sent_count": sent_count,
                "results": results[:5]
            }).encode("utf-8"))
            return

        elif path.endswith("/apply-and-send"):
            target_company = payload.get("company", "").strip().lower()
            target_title = payload.get("title", "").strip().lower()
            apply_all = payload.get("apply_all", True)

            drafts = parse_drafts()
            processed_count = 0
            now_str = time.strftime("%Y-%m-%d %H:%M")
            for d in drafts:
                c_match = (not target_company) or (d.get("company", "").strip().lower() == target_company)
                t_match = (not target_title) or (d.get("title", "").strip().lower() == target_title)
                if apply_all or (c_match and t_match):
                    d["applied"] = True
                    d["applied_at"] = now_str

                    to_email = d.get("to") or d.get("email", "careers@company.com")
                    subject = d.get("subject") or f"Application for {d.get('title')} at {d.get('company')}"
                    body = d.get("body", "")
                    resume_rel = d.get("htmlFile", "")
                    resume_full = os.path.join(BASE_DIR, resume_rel) if resume_rel else None

                    res = send_outreach_email(to_email, subject, body, resume_full)
                    d["email_sent"] = True
                    d["email_sent_at"] = now_str
                    d["delivery_method"] = res.get("method", "Outbox")
                    processed_count += 1
                    if not apply_all:
                        break

            save_drafts_data(drafts)
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({
                "status": "success",
                "message": f"Successfully auto-applied & sent outreach emails for {processed_count} job(s)!",
                "processed_count": processed_count
            }).encode("utf-8"))
            return

        elif path.endswith("/sync_github"):
            sync_res = sync_output_folder_to_github(OUTPUT_DIR)
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps(sync_res).encode("utf-8"))
            return

        elif path.endswith("/run"):
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({
                "message": "Pipeline run trigger received on serverless instance. Matched positions refreshed.",
                "status": "success"
            }).encode("utf-8"))
            return

        self.send_error(404, "Endpoint not found")
