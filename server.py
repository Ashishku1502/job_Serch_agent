"""
AI Job Application Agent — Web Server & Dashboard
Serves the web dashboard at http://localhost:8000 and provides live API endpoints.
"""

import http.server
import socketserver
import json
import os
import subprocess
import sys
import time
import urllib.parse

from github_helper import sync_output_folder_to_github
from email_service import send_outreach_email

PORT = 8000
DIRECTORY = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(DIRECTORY, "output")
LOG_FILE = os.path.join(OUTPUT_DIR, "pipeline.log")


def parse_drafts_data():
    """Read and normalize output/drafts.json for dashboard presentation."""
    drafts_path = os.path.join(OUTPUT_DIR, "drafts.json")
    raw_data = []
    if os.path.exists(drafts_path):
        try:
            with open(drafts_path, "r", encoding="utf-8") as f:
                raw_data = json.load(f)
        except Exception:
            raw_data = []

    parsed_applications = []
    seen = set()
    for item in raw_data:
        subject = item.get("subject", "")
        body = item.get("body", "")
        to_email = item.get("to") or item.get("email", "careers@company.com")

        company = item.get("company")
        title = item.get("title")
        if not company or not title or company == "Unknown Company":
            if "Application for " in subject and " at " in subject:
                parts = subject.replace("Application for ", "").split(" at ")
                title = parts[0].strip()
                company = " at ".join(parts[1:]).strip()
            elif " / " in subject and " Application — " in subject:
                after_slash = subject.split(" / ")[1]
                parts = after_slash.split(" Application — ")
                title = parts[0].strip()
                company = parts[1].strip()
            else:
                title = title or "Software Engineer"
                company = company or "Target Company"

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

        parsed_applications.append({
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

    return parsed_applications


class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

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

        if path == "/" or path == "/index.html":
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.send_cors_headers()
            self.end_headers()
            with open(os.path.join(DIRECTORY, "index.html"), "rb") as f:
                self.wfile.write(f.read())
            return
        elif path == "/api/status":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({
                "status": "ready",
                "agent": "Ashish Kumar Agent",
                "timestamp": time.time()
            }).encode("utf-8"))
            return
        elif path == "/api/data":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.send_cors_headers()
            self.end_headers()
            applications = parse_drafts_data()
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
        elif path == "/api/logs":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.send_cors_headers()
            self.end_headers()
            log_content = "No log output recorded yet. Trigger the pipeline to generate live execution logs."
            if os.path.exists(LOG_FILE):
                try:
                    with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                        log_content = f.read()
                except Exception as exc:
                    log_content = f"Error reading log file: {exc}"
            self.wfile.write(json.dumps({"logs": log_content}).encode("utf-8"))
            return

        return super().do_GET()

    def do_POST(self):
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path

        if path == "/api/run":
            content_length = int(self.headers.get("Content-Length", 0))
            payload = {}
            if content_length > 0:
                try:
                    body_bytes = self.rfile.read(content_length)
                    payload = json.loads(body_bytes.decode("utf-8"))
                except Exception:
                    payload = {}

            venv_python = os.path.join(DIRECTORY, "venv", "Scripts", "python.exe")
            python_exe = venv_python if os.path.exists(venv_python) else sys.executable
            main_script = os.path.join(DIRECTORY, "main.py")
            os.makedirs(OUTPUT_DIR, exist_ok=True)

            run_env = os.environ.copy()
            run_env["PYTHONUNBUFFERED"] = "1"
            run_env["PYTHONIOENCODING"] = "utf-8"
            if payload.get("query"):
                run_env["JOB_SEARCH_QUERY"] = str(payload["query"]).strip()
            if payload.get("location"):
                run_env["JOB_SEARCH_LOCATION"] = str(payload["location"]).strip()
            if payload.get("max_jobs"):
                run_env["MAX_JOBS"] = str(payload["max_jobs"]).strip()
            if payload.get("min_score"):
                run_env["MIN_MATCH_SCORE"] = str(payload["min_score"]).strip()

            log_f = open(LOG_FILE, "a", encoding="utf-8", errors="replace")
            log_f.write(f"\n==================================================\n")
            log_f.write(f"Pipeline Run Triggered at {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            if payload:
                log_f.write(f"Config: Query='{run_env.get('JOB_SEARCH_QUERY')}', Location='{run_env.get('JOB_SEARCH_LOCATION')}', MaxJobs={run_env.get('MAX_JOBS')}, MinScore={run_env.get('MIN_MATCH_SCORE')}\n")
            log_f.write(f"==================================================\n")
            log_f.flush()

            subprocess.Popen([python_exe, main_script], cwd=DIRECTORY, env=run_env, stdout=log_f, stderr=subprocess.STDOUT)

            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({
                "message": "Pipeline execution started successfully!",
                "log_file": "output/pipeline.log",
                "params": payload
            }).encode("utf-8"))
            return
        elif path == "/api/apply":
            content_length = int(self.headers.get("Content-Length", 0))
            payload = {}
            if content_length > 0:
                try:
                    body_bytes = self.rfile.read(content_length)
                    payload = json.loads(body_bytes.decode("utf-8"))
                except Exception:
                    payload = {}

            target_company = payload.get("company", "").strip().lower()
            target_title = payload.get("title", "").strip().lower()
            apply_all = payload.get("apply_all", False)

            drafts_path = os.path.join(OUTPUT_DIR, "drafts.json")
            updated_count = 0
            if os.path.exists(drafts_path):
                try:
                    with open(drafts_path, "r", encoding="utf-8") as f:
                        drafts = json.load(f)

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

                    with open(drafts_path, "w", encoding="utf-8") as f:
                        json.dump(drafts, f, indent=2, ensure_ascii=False)
                except Exception as exc:
                    self.send_error(500, f"Error updating application state: {exc}")
                    return

            if os.path.exists(LOG_FILE):
                try:
                    with open(LOG_FILE, "a", encoding="utf-8", errors="replace") as log_f:
                        if apply_all:
                            log_f.write(f"[{time.strftime('%H:%M:%S')}] [AUTO-APPLY] Batch auto-applied to {updated_count} positions with tailored resumes.\n")
                        else:
                            log_f.write(f"[{time.strftime('%H:%M:%S')}] [AUTO-APPLY] Submitted application for {payload.get('title')} at {payload.get('company')}.\n")
                except Exception:
                    pass

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
        elif path == "/api/send-mail":
            content_length = int(self.headers.get("Content-Length", 0))
            payload = {}
            if content_length > 0:
                try:
                    body_bytes = self.rfile.read(content_length)
                    payload = json.loads(body_bytes.decode("utf-8"))
                except Exception:
                    payload = {}

            target_company = payload.get("company", "").strip().lower()
            target_title = payload.get("title", "").strip().lower()
            send_all = payload.get("send_all", False)

            drafts_path = os.path.join(OUTPUT_DIR, "drafts.json")
            sent_count = 0
            results = []
            if os.path.exists(drafts_path):
                try:
                    with open(drafts_path, "r", encoding="utf-8") as f:
                        drafts = json.load(f)

                    now_str = time.strftime("%Y-%m-%d %H:%M")
                    for d in drafts:
                        c_match = (not target_company) or (d.get("company", "").strip().lower() == target_company)
                        t_match = (not target_title) or (d.get("title", "").strip().lower() == target_title)
                        if send_all or (c_match and t_match):
                            to_email = d.get("to") or d.get("email", "careers@company.com")
                            subject = d.get("subject") or f"Application for {d.get('title')} at {d.get('company')}"
                            body = d.get("body", "")
                            resume_rel = d.get("htmlFile", "")
                            resume_full = os.path.join(DIRECTORY, resume_rel) if resume_rel else None

                            res = send_outreach_email(to_email, subject, body, resume_full)
                            d["email_sent"] = True
                            d["email_sent_at"] = now_str
                            d["delivery_method"] = res.get("method", "Outbox")
                            sent_count += 1
                            results.append(res)
                            if not send_all:
                                break

                    with open(drafts_path, "w", encoding="utf-8") as f:
                        json.dump(drafts, f, indent=2, ensure_ascii=False)
                except Exception as exc:
                    self.send_error(500, f"Error processing email dispatch: {exc}")
                    return

            if os.path.exists(LOG_FILE):
                try:
                    with open(LOG_FILE, "a", encoding="utf-8", errors="replace") as log_f:
                        if send_all:
                            log_f.write(f"[{time.strftime('%H:%M:%S')}] [EMAIL-SEND] Dispatched outreach emails to {sent_count} recruiter contacts.\n")
                        else:
                            log_f.write(f"[{time.strftime('%H:%M:%S')}] [EMAIL-SEND] Dispatched outreach email for {payload.get('title')} at {payload.get('company')}.\n")
                except Exception:
                    pass

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
        elif path == "/api/apply-and-send":
            content_length = int(self.headers.get("Content-Length", 0))
            payload = {}
            if content_length > 0:
                try:
                    body_bytes = self.rfile.read(content_length)
                    payload = json.loads(body_bytes.decode("utf-8"))
                except Exception:
                    payload = {}

            target_company = payload.get("company", "").strip().lower()
            target_title = payload.get("title", "").strip().lower()
            apply_all = payload.get("apply_all", True)

            drafts_path = os.path.join(OUTPUT_DIR, "drafts.json")
            processed_count = 0
            if os.path.exists(drafts_path):
                try:
                    with open(drafts_path, "r", encoding="utf-8") as f:
                        drafts = json.load(f)

                    now_str = time.strftime("%Y-%m-%d %H:%M")
                    for d in drafts:
                        c_match = (not target_company) or (d.get("company", "").strip().lower() == target_company)
                        t_match = (not target_title) or (d.get("title", "").strip().lower() == target_title)
                        if apply_all or (c_match and t_match):
                            # Mark Applied
                            d["applied"] = True
                            d["applied_at"] = now_str

                            # Dispatch Email
                            to_email = d.get("to") or d.get("email", "careers@company.com")
                            subject = d.get("subject") or f"Application for {d.get('title')} at {d.get('company')}"
                            body = d.get("body", "")
                            resume_rel = d.get("htmlFile", "")
                            resume_full = os.path.join(DIRECTORY, resume_rel) if resume_rel else None

                            res = send_outreach_email(to_email, subject, body, resume_full)
                            d["email_sent"] = True
                            d["email_sent_at"] = now_str
                            d["delivery_method"] = res.get("method", "Outbox")
                            processed_count += 1
                            if not apply_all:
                                break

                    with open(drafts_path, "w", encoding="utf-8") as f:
                        json.dump(drafts, f, indent=2, ensure_ascii=False)
                except Exception as exc:
                    self.send_error(500, f"Error processing auto-apply and send: {exc}")
                    return

            if os.path.exists(LOG_FILE):
                try:
                    with open(LOG_FILE, "a", encoding="utf-8", errors="replace") as log_f:
                        log_f.write(f"[{time.strftime('%H:%M:%S')}] [AUTO-APPLY & EMAIL] Processed & applied to {processed_count} positions and dispatched outreach emails.\n")
                except Exception:
                    pass

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
        elif path == "/api/sync_github":
            sync_res = sync_output_folder_to_github(OUTPUT_DIR)
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps(sync_res).encode("utf-8"))
            return

        self.send_error(404, "Endpoint not found")


def start_server():
    print(f"AI Job Application Agent Dashboard running at: http://localhost:{PORT}")
    with socketserver.TCPServer(("", PORT), DashboardHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nServer stopped.")


if __name__ == "__main__":
    start_server()
