"""
GitHub Sync Helper
==================
Helper functions to automatically commit and sync generated job search artifacts
(tailored HTML resumes, drafts.json, logs) to a GitHub repository.

Supports two sync strategies:
1. GitHub REST API (recommended if GITHUB_TOKEN is set in .env)
2. Local Git CLI fallback (uses git commit & push)
"""

import os
import glob
import base64
import logging
import subprocess
from typing import Dict, Any, List, Optional
import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("github_helper")

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = os.getenv("GITHUB_REPO", "Ashishku1502/job_Serch_agent")  # e.g., 'username/repo'
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")

SENDER_NAME = os.getenv("SENDER_NAME", "Ashish Kumar")


def _get_clean_repo_slug(repo: str) -> str:
    """Extract owner/repo format from full URL or slug."""
    repo = repo.strip()
    if repo.startswith("http"):
        parts = repo.rstrip("/").replace(".git", "").split("/")
        if len(parts) >= 2:
            return f"{parts[-2]}/{parts[-1]}"
    return repo


def upload_file_via_github_api(
    local_file_path: str,
    repo_file_path: Optional[str] = None,
    commit_message: Optional[str] = None,
) -> bool:
    """
    Upload or update a single file in a GitHub repository using the GitHub REST API.
    """
    if not GITHUB_TOKEN:
        logger.debug("GITHUB_TOKEN not provided. Skipping GitHub API upload.")
        return False

    repo_slug = _get_clean_repo_slug(GITHUB_REPO)
    if not repo_slug or "/" not in repo_slug:
        logger.warning("Invalid GITHUB_REPO: %s. Expected 'owner/repo'.", GITHUB_REPO)
        return False

    if not os.path.exists(local_file_path):
        logger.warning("Local file does not exist: %s", local_file_path)
        return False

    if not repo_file_path:
        repo_file_path = local_file_path.replace("\\", "/").lstrip("./")

    if not commit_message:
        commit_message = f"chore(auto-sync): update {os.path.basename(local_file_path)}"

    url = f"https://api.github.com/repos/{repo_slug}/contents/{repo_file_path}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }

    try:
        with open(local_file_path, "rb") as f:
            content_bytes = f.read()
        encoded_content = base64.b64encode(content_bytes).decode("utf-8")

        # Check if file already exists to get its sha
        sha = None
        get_resp = requests.get(url, headers=headers, params={"ref": GITHUB_BRANCH}, timeout=15)
        if get_resp.status_code == 200:
            sha = get_resp.json().get("sha")

        payload = {
            "message": commit_message,
            "content": encoded_content,
            "branch": GITHUB_BRANCH,
        }
        if sha:
            payload["sha"] = sha

        put_resp = requests.put(url, headers=headers, json=payload, timeout=20)
        if put_resp.status_code in (200, 201):
            logger.info("Successfully synced %s to GitHub repo %s via API.", repo_file_path, repo_slug)
            return True
        else:
            logger.warning(
                "Failed to upload %s via API (%d): %s",
                repo_file_path,
                put_resp.status_code,
                put_resp.text,
            )
            return False
    except Exception as exc:
        logger.warning("Error uploading %s to GitHub API: %s", local_file_path, exc)
        return False


def sync_via_git_cli(commit_message: str = "auto-sync: save job application data") -> bool:
    """
    Fallback method: Commit and push output files using local Git CLI.
    """
    repo_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        # Check if git is available
        res = subprocess.run(
            ["git", "status"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            logger.warning("Git CLI is not initialized or repo error: %s", res.stderr)
            return False

        subprocess.run(["git", "add", "output/"], cwd=repo_dir, check=False)
        commit_res = subprocess.run(
            ["git", "commit", "-m", commit_message],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        if "nothing to commit" in commit_res.stdout:
            logger.info("No changes to commit in git working tree.")
            return True

        push_res = subprocess.run(
            ["git", "push", "origin", GITHUB_BRANCH],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        if push_res.returncode == 0:
            logger.info("Successfully pushed changes to GitHub via Git CLI.")
            return True
        else:
            logger.warning("Git push failed: %s", push_res.stderr)
            return False
    except Exception as exc:
        logger.warning("Git CLI sync exception: %s", exc)
        return False


def sync_output_folder_to_github(output_dir: str = "output") -> Dict[str, Any]:
    """
    Sync all files inside output_dir (HTML resumes, drafts.json, etc.) to GitHub.
    Uses API if GITHUB_TOKEN is configured; otherwise falls back to Git CLI.
    """
    if not os.path.exists(output_dir):
        logger.info("Output directory '%s' does not exist yet. Nothing to sync.", output_dir)
        return {"synced_count": 0, "status": "skipped", "message": "Output directory not found"}

    files = glob.glob(os.path.join(output_dir, "*"))
    if not files:
        logger.info("No files in '%s' to sync.", output_dir)
        return {"synced_count": 0, "status": "skipped", "message": "No files to sync"}

    logger.info("Starting GitHub sync for %d file(s) in %s...", len(files), output_dir)

    success_count = 0
    if GITHUB_TOKEN:
        for fpath in files:
            if os.path.isfile(fpath):
                rel_path = os.path.relpath(fpath, start=os.path.dirname(os.path.abspath(__file__)))
                rel_path = rel_path.replace("\\", "/")
                msg = f"auto-sync: save {os.path.basename(fpath)}"
                if upload_file_via_github_api(fpath, repo_file_path=rel_path, commit_message=msg):
                    success_count += 1
        status_msg = f"Synced {success_count}/{len(files)} files via GitHub REST API"
        logger.info(status_msg)
        return {"synced_count": success_count, "total_files": len(files), "status": "success", "message": status_msg}
    else:
        logger.info("GITHUB_TOKEN not set. Attempting sync via local Git CLI...")
        cli_success = sync_via_git_cli("auto-sync: save job agent output data")
        if cli_success:
            return {"synced_count": len(files), "total_files": len(files), "status": "success", "message": "Synced via Git CLI"}
        else:
            return {"synced_count": 0, "total_files": len(files), "status": "partial/warning", "message": "Git CLI sync attempted; set GITHUB_TOKEN in .env for direct API sync"}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = sync_output_folder_to_github()
    print("Sync Result:", result)
