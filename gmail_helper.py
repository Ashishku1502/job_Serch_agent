"""
Gmail helper — replaces the "Create draft" node from the n8n workflow.

Uses the Gmail API with the same OAuth2 credentials/token flow as
google_drive_helper.py (add the gmail.compose scope to that flow, or run
your own separate auth — see SCOPES below).
"""

import base64
import os
from email.mime.text import MIMEText

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]

CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
GMAIL_TOKEN_FILE = os.getenv("GMAIL_TOKEN_FILE", "gmail_token.json")


def _get_credentials() -> Credentials:
    creds = None
    if os.path.exists(GMAIL_TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(GMAIL_TOKEN_FILE, SCOPES)
        except Exception:
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None

        if not creds or not creds.valid:
            if not os.path.exists(CREDENTIALS_FILE):
                raise FileNotFoundError(
                    f"Google OAuth credentials file '{CREDENTIALS_FILE}' not found. "
                    "Please place your OAuth client secrets JSON file as 'credentials.json' "
                    "or set GOOGLE_CREDENTIALS_FILE."
                )
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(GMAIL_TOKEN_FILE, "w") as token_file:
            token_file.write(creds.to_json())

    return creds


def _gmail_service():
    return build("gmail", "v1", credentials=_get_credentials())


def create_gmail_draft(to: str, subject: str, body: str) -> str:
    """Create a Gmail draft and return its draft id."""
    service = _gmail_service()

    message = MIMEText(body, "plain", "utf-8")
    message["to"] = to
    message["subject"] = subject
    raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

    draft = service.users().drafts().create(
        userId="me",
        body={"message": {"raw": raw_message}},
    ).execute()

    return draft["id"]
