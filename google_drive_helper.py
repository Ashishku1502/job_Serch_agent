"""
Google Drive / Docs helper functions.

These replace the "Google Drive: Download resume", "Convert to document",
and "Share file" nodes from the n8n workflow, using the official
google-api-python-client libraries with OAuth2 user credentials.

Setup:
    1. Enable the Google Drive API and Google Docs API in Google Cloud Console.
    2. Create an OAuth 2.0 Client ID (Desktop app) and download it as
       credentials.json into this project's directory.
    3. On first run, a browser window opens for consent and a token.json
       file is cached for subsequent runs.
"""

import os
import io
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
]

CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
TOKEN_FILE = os.getenv("GOOGLE_TOKEN_FILE", "token.json")


def _get_credentials() -> Credentials:
    creds = None
    if os.path.exists(TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
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

        with open(TOKEN_FILE, "w") as token_file:
            token_file.write(creds.to_json())

    return creds


def _drive_service():
    return build("drive", "v3", credentials=_get_credentials())


def _docs_service():
    return build("docs", "v1", credentials=_get_credentials())


def download_file_as_text(file_id: str) -> str:
    """Download a Google Doc's contents as plain text, or read local file if path exists."""
    if os.path.exists(file_id):
        with open(file_id, "r", encoding="utf-8") as f:
            return f.read()

    drive = _drive_service()
    try:
        request = drive.files().export_media(fileId=file_id, mimeType="text/plain")
        buffer = io.BytesIO()
        downloader = MediaIoBaseDownload(buffer, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buffer.getvalue().decode("utf-8")
    except Exception:
        # Fallback to get_media for non-Workspace files stored in Drive
        request = drive.files().get_media(fileId=file_id)
        buffer = io.BytesIO()
        downloader = MediaIoBaseDownload(buffer, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return buffer.getvalue().decode("utf-8")


def create_google_doc_from_html(html_content: str, title: str, folder_id: Optional[str] = None) -> str:
    """
    Create a Google Doc from HTML content by uploading the HTML and asking
    Drive to convert it, then optionally moving it into a target folder.
    Returns the new document's file id.
    """
    drive = _drive_service()

    file_metadata = {
        "name": title,
        "mimeType": "application/vnd.google-apps.document",
    }
    if folder_id:
        file_metadata["parents"] = [folder_id]

    media = MediaIoBaseUpload(
        io.BytesIO(html_content.encode("utf-8")),
        mimetype="text/html",
        resumable=True,
    )

    created = drive.files().create(body=file_metadata, media_body=media, fields="id").execute()
    return created["id"]


def share_file(file_id: str, role: str = "reader", type_: str = "anyone") -> None:
    """Share a Drive file. Defaults to 'anyone with the link can view'."""
    drive = _drive_service()
    drive.permissions().create(
        fileId=file_id,
        body={"role": role, "type": type_},
        fields="id",
    ).execute()


def export_doc_as_pdf(file_id: str) -> bytes:
    """Export a Google Doc as a PDF and return the raw bytes."""
    drive = _drive_service()
    request = drive.files().export_media(fileId=file_id, mimeType="application/pdf")
    buffer = io.BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buffer.getvalue()

