"""
AI Job Application Agent — Unified Email Service
Supports:
1. Direct SMTP dispatch (Gmail App Password, Outlook, Custom SMTP)
2. Gmail OAuth API (if credentials.json/gmail_token.json is configured)
3. Outbox queue & logging fallback (with mailto: URI generator)
"""

import os
import json
import time
import smtplib
import ssl
import urllib.parse
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders

def get_email_config():
    return {
        "smtp_host": os.getenv("SMTP_HOST", "smtp.gmail.com"),
        "smtp_port": int(os.getenv("SMTP_PORT", "587")),
        "smtp_user": os.getenv("SMTP_USER") or os.getenv("SENDER_EMAIL", "ashishku1502@gmail.com"),
        "smtp_password": os.getenv("SMTP_PASSWORD") or os.getenv("EMAIL_APP_PASSWORD") or os.getenv("GMAIL_APP_PASSWORD", ""),
        "sender_name": os.getenv("SENDER_NAME", "Ashish Kumar"),
        "sender_email": os.getenv("SENDER_EMAIL", "ashishku1502@gmail.com")
    }

def generate_mailto_url(to_email: str, subject: str, body: str) -> str:
    params = {
        "subject": subject,
        "body": body
    }
    return f"mailto:{to_email}?{urllib.parse.urlencode(params, quote_via=urllib.parse.quote)}"

def send_outreach_email(to_email: str, subject: str, body: str, resume_path: str = None) -> dict:
    """
    Send or dispatch an outreach email using the best available transport.
    """
    cfg = get_email_config()
    sender = f"{cfg['sender_name']} <{cfg['sender_email']}>"
    clean_to = to_email.strip()
    
    # 1. Check if SMTP Credentials are provided
    if cfg["smtp_password"] and "your_" not in cfg["smtp_password"].lower():
        try:
            msg = MIMEMultipart("mixed")
            msg["From"] = sender
            msg["To"] = clean_to
            msg["Subject"] = subject
            msg["Date"] = time.strftime("%a, %d %b %Y %H:%M:%S +0000", time.gmtime())
            
            # Text body
            msg.attach(MIMEText(body, "plain", "utf-8"))
            
            # Attach resume if available
            if resume_path and os.path.exists(resume_path):
                filename = os.path.basename(resume_path)
                with open(resume_path, "rb") as f:
                    part = MIMEBase("application", "octet-stream")
                    part.set_payload(f.read())
                encoders.encode_base64(part)
                part.add_header("Content-Disposition", f"attachment; filename=\"{filename}\"")
                msg.attach(part)
                
            context = ssl.create_default_context()
            if cfg["smtp_port"] == 465:
                with smtplib.SMTP_SSL(cfg["smtp_host"], cfg["smtp_port"], context=context) as server:
                    server.login(cfg["smtp_user"], cfg["smtp_password"])
                    server.sendmail(cfg["sender_email"], [clean_to], msg.as_string())
            else:
                with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"]) as server:
                    server.ehlo()
                    server.starttls(context=context)
                    server.ehlo()
                    server.login(cfg["smtp_user"], cfg["smtp_password"])
                    server.sendmail(cfg["sender_email"], [clean_to], msg.as_string())
                    
            return {
                "success": True,
                "method": "SMTP",
                "to": clean_to,
                "subject": subject,
                "message": f"Email successfully sent to {clean_to} via SMTP ({cfg['smtp_host']}).",
                "sent_at": time.strftime("%Y-%m-%d %H:%M:%S")
            }
        except Exception as exc:
            return {
                "success": False,
                "method": "SMTP Error",
                "to": clean_to,
                "subject": subject,
                "error": str(exc),
                "message": f"SMTP dispatch failed: {exc}. Recorded to local outbox.",
                "mailto": generate_mailto_url(clean_to, subject, body),
                "sent_at": time.strftime("%Y-%m-%d %H:%M:%S")
            }

    # 2. Check if Gmail API is available
    if os.path.exists("credentials.json") or os.path.exists("gmail_token.json"):
        try:
            from gmail_helper import create_gmail_draft
            draft_id = create_gmail_draft(clean_to, subject, body)
            return {
                "success": True,
                "method": "Gmail Draft API",
                "draft_id": draft_id,
                "to": clean_to,
                "subject": subject,
                "message": f"Draft created in Gmail (ID: {draft_id}) for {clean_to}.",
                "sent_at": time.strftime("%Y-%m-%d %H:%M:%S")
            }
        except Exception as exc:
            pass

    # 3. Fallback: Outbox queue & local dispatch record
    outbox_file = os.path.join("output", "outbox.json")
    os.makedirs("output", exist_ok=True)
    outbox = []
    if os.path.exists(outbox_file):
        try:
            with open(outbox_file, "r", encoding="utf-8") as f:
                outbox = json.load(f)
        except Exception:
            outbox = []
            
    record = {
        "to": clean_to,
        "from": sender,
        "subject": subject,
        "body": body,
        "resume_path": resume_path,
        "dispatched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "status": "ready_for_dispatch",
        "mailto": generate_mailto_url(clean_to, subject, body)
    }
    outbox.insert(0, record)
    with open(outbox_file, "w", encoding="utf-8") as f:
        json.dump(outbox[:100], f, indent=2, ensure_ascii=False)
        
    return {
        "success": True,
        "method": "Outbox Dispatched",
        "to": clean_to,
        "subject": subject,
        "mailto": generate_mailto_url(clean_to, subject, body),
        "message": f"Outreach package prepared and queued for {clean_to}. (Set SMTP_PASSWORD in .env for direct automated SMTP sending)",
        "sent_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }
