import os
import hmac
import hashlib
import sys
import json
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env")

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from dashboard.database import init_db, save_release, get_all_releases, get_release_by_id
from agent.classifier import classify_release
from agent.github_client import get_commits_between_tags, create_release_draft
from agent.release_notes import generate_release_notes
from agent.major_release import build_major_release_body
from agent.notifier import send_release_notification

app = FastAPI(title="RelaxRelease")
templates = Jinja2Templates(
    directory=str(Path(__file__).parent / "templates")
)


from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    print("✅ RelaxRelease dashboard started.")
    yield

app = FastAPI(title="RelaxRelease", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    releases = get_all_releases()
    return templates.TemplateResponse("index.html", {
        "request": request,
        "releases": releases
    })


@app.get("/release/{release_id}", response_class=HTMLResponse)
async def release_detail(request: Request, release_id: int):
    release = get_release_by_id(release_id)
    if not release:
        raise HTTPException(status_code=404, detail="Release not found")
    return templates.TemplateResponse("release.html", {
        "request": request,
        "release": release
    })


@app.get("/settings", response_class=HTMLResponse)
async def settings(request: Request):
    return HTMLResponse("""
    <html>
    <head><title>Settings — RelaxRelease</title></head>
    <body style="font-family:sans-serif;background:#0d1117;color:#e6edf3;padding:32px;">
        <h2>⚙️ Settings</h2>
        <p style="color:#8b949e;margin-top:8px;">
            Settings are configured via environment variables in Koyeb.<br><br>
            <strong>GEMINI_API_KEY</strong> — Your Gemini API key<br>
            <strong>GITHUB_TOKEN</strong> — Your GitHub personal access token<br>
            <strong>NOTIFY_EMAIL</strong> — Email to notify when draft is ready<br>
            <strong>RESEND_API_KEY</strong> — Your Resend API key<br>
            <strong>GITHUB_WEBHOOK_SECRET</strong> — Your webhook secret<br>
        </p>
        <br>
        <a href="/" style="color:#58a6ff;">← Back to releases</a>
    </body>
    </html>
    """)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "RelaxRelease"}


@app.post("/webhook")
async def webhook(request: Request):
    body = await request.body()

    # Verify GitHub signature
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET")
    if secret:
        signature = request.headers.get("X-Hub-Signature-256", "")
        mac = hmac.new(
            secret.encode("utf-8"),
            msg=body,
            digestmod=hashlib.sha256
        )
        expected = "sha256=" + mac.hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise HTTPException(status_code=401, detail="Invalid signature")

    # Only handle push events
    event = request.headers.get("X-GitHub-Event", "")
    if event != "push" and event != "create":
        return JSONResponse({"message": f"Ignoring event: {event}"})

    payload = await request.json() if not body else __import__("json").loads(body)

    # Handle tag creation event
    ref = payload.get("ref", "")

    # Handle both push and create events for tags
    if event == "create":
        if payload.get("ref_type") != "tag":
            return JSONResponse({"message": "Not a tag — ignoring"})
        tag = ref
    else:
        if not ref.startswith("refs/tags/"):
            return JSONResponse({"message": "Not a tag push — ignoring"})
        tag = ref.replace("refs/tags/", "")

    repo = payload.get("repository", {}).get("full_name", "")

    print(f"✅ Tag received: {tag} on {repo}")

    # Run agent in background
    import threading
    thread = threading.Thread(
        target=run_agent_background,
        args=(repo, tag),
        daemon=True
    )
    thread.start()

    return JSONResponse({
        "message": "Webhook received — processing",
        "tag": tag,
        "repo": repo
    })


def run_agent_background(repo: str, new_tag: str):
    """Runs the release agent in a background thread."""
    try:
        github_token = os.environ.get("GITHUB_TOKEN")
        if not github_token:
            print("❌ GITHUB_TOKEN not set")
            return

        # Get all tags to find previous one
        import requests as req
        response = req.get(
            f"https://api.github.com/repos/{repo}/tags",
            headers={
                "Authorization": f"Bearer {github_token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28"
            },
            timeout=30
        )

        tags = [t["name"] for t in response.json()]
        if new_tag in tags:
            idx = tags.index(new_tag)
            previous_tag = tags[idx + 1] if idx + 1 < len(tags) else None
        else:
            previous_tag = tags[0] if tags else None

        if not previous_tag:
            print(f"⚠️ No previous tag found for {new_tag}")
            return

        # Classify release
        release_type = classify_release(previous_tag, new_tag)
        print(f"📋 Release type: {release_type.upper()}")

        # Fetch commits
        commits = get_commits_between_tags(repo, previous_tag, new_tag, github_token)
        if not commits:
            print("⚠️ No commits found between tags.")
            return

        # Generate notes
        if release_type == "major":
            notes = build_major_release_body(commits, previous_tag, new_tag, repo)
        else:
            notes = generate_release_notes(commits, new_tag, repo)

        # Create draft
        release_name = f"{'🚀 Major Release' if release_type == 'major' else 'Release'} {new_tag}"
        release = create_release_draft(repo, new_tag, notes, github_token, release_name)
        draft_url = release["html_url"]

        # Save to database
        save_release(
            repo=repo,
            tag=new_tag,
            previous_tag=previous_tag,
            release_type=release_type,
            draft_url=draft_url,
            release_notes=notes,
            status="draft"
        )

        print(f"✅ Release draft created: {draft_url}")

        # Send notification
        recipient = os.environ.get("NOTIFY_EMAIL")
        if recipient:
            send_release_notification(release_type, new_tag, repo, draft_url, recipient)

    except Exception as e:
        print(f"❌ Agent error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)