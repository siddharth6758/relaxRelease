import os
import asyncio
import hmac
import hashlib
import sys
import requests
import uuid
from pathlib import Path
from typing import List
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException, Response, Form, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from .auth import (
    sign_in, sign_up, sign_out,
    get_oauth_url, get_user,
    get_current_user, require_auth, get_github_token, get_user_provider
)
from .billing import router as billing_router
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env")

SUPABASE_URL = os.environ.get("SUPABASE_URL")

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from dashboard.database import (
    init_db, save_release, get_all_releases, get_release_by_id,
    check_plan_limits, get_expired_paid_users, enforce_free_tier_on_expiry, cancel_subscription, add_repository, list_repositories, delete_repository, get_user_plan, get_user_id_by_repo, create_ticket, get_ticket_by_id, get_tickets_by_user, upload_ticket_images
)
from agent.classifier import classify_release
from agent.github_client import get_commits_between_tags, create_release_draft, create_webhook, delete_webhook
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
    async def expiry_checker():
        while True:
            await asyncio.sleep(3600)  # run every hour
            try:
                expired = get_expired_paid_users()
                for user_id in expired:
                    enforce_free_tier_on_expiry(user_id)
                    cancel_subscription(user_id)  # marks status=cancelled in DB
                    print(f"⏰ Plan expired + downgraded: {user_id}")
            except Exception as e:
                print(f"❌ Expiry checker error: {e}")

    asyncio.create_task(expiry_checker())
    print("✅ RelaxRelease dashboard started.")
    yield

app = FastAPI(title="RelaxRelease", lifespan=lifespan)
app.include_router(billing_router)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    releases = get_all_releases(user["id"])
    user["plan"] = get_user_plan(user["id"])
    return templates.TemplateResponse("index.html", {
        "request": request,
        "releases": releases,
        "user": user
    })


@app.get("/release/{release_id}", response_class=HTMLResponse)
async def release_detail(request: Request, release_id: int):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    release = get_release_by_id(release_id)
    if not release:
        raise HTTPException(status_code=404, detail="Release not found")
    return templates.TemplateResponse("release.html", {
        "request": request,
        "release": release,
        "user": user
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

    user_id = get_user_id_by_repo(repo)

    # Run agent in background
    import threading
    thread = threading.Thread(
        target=run_agent_background,
        args=(repo, tag, user_id),
        daemon=True
    )
    thread.start()

    return JSONResponse({
        "message": "Webhook received — processing",
        "tag": tag,
        "repo": repo
    })


def run_agent_background(repo: str, new_tag: str, user_id: str = None):
    """Runs the release agent in a background thread."""
    try:
        github_token = os.environ.get("GITHUB_TOKEN")
        if not github_token:
            print("❌ GITHUB_TOKEN not set")
            return

        # Plan limit enforcement
        if user_id:
            guard = check_plan_limits(user_id)
            if not guard["allowed"]:
                print(f"🚫 Plan limit hit for user {user_id}: {guard['reason']}")
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
            status="draft",
            user_id=user_id,
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

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = None):
    user = get_current_user(request)
    if user:
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse("login.html", {
        "request": request,
        "user": None,
        "error": error
    })


@app.post("/login")
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...)
):
    result = sign_in(email, password)
    if "access_token" in result:
        response = RedirectResponse("/", status_code=302)
        response.set_cookie(
            "access_token",
            result["access_token"],
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=3600
        )
        response.set_cookie(
            "refresh_token",
            result["refresh_token"],
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=604800
        )
        return response
    error = result.get("error_description") or result.get("msg") or "Invalid credentials"
    return RedirectResponse(f"/login?error={error}", status_code=302)


@app.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request, error: str = None, success: str = None):
    user = get_current_user(request)
    if user:
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse("signup.html", {
        "request": request,
        "user": None,
        "error": error,
        "success": success
    })


@app.post("/signup")
async def signup(
    request: Request,
    email: str = Form(...),
    password: str = Form(...)
):
    result = sign_up(email, password)
    if "id" in result:
        return RedirectResponse(
            "/signup?success=Account created! Check your email to verify.",
            status_code=302
        )
    error = result.get("msg") or result.get("error_description") or "Signup failed"
    return RedirectResponse(f"/signup?error={error}", status_code=302)


@app.get("/auth/oauth/{provider}")
async def oauth_login(provider: str):
    if provider not in ("github", "google"):
        raise HTTPException(status_code=400, detail="Invalid provider")
    url = get_oauth_url(provider)
    return RedirectResponse(url, status_code=302)

@app.post("/auth/session")
async def set_session(data: dict, response: Response):
    response.set_cookie("access_token", data["access_token"],
        httponly=True, secure=True, samesite="lax", max_age=3600)
    response.set_cookie("refresh_token", data["refresh_token"],
        httponly=True, secure=True, samesite="lax", max_age=604800)

    if data.get("provider_token") and data.get("provider") == "github":
        response.set_cookie("github_token", data["provider_token"],
            httponly=True, secure=True, samesite="lax", max_age=604800)

    return {"ok": True}

@app.get("/auth/callback")
async def auth_callback(request: Request):
    """Supabase redirects here after OAuth login."""
    return templates.TemplateResponse(
        "callback.html",
        {
            "request": request,
            "error": None
        }
    )


@app.get("/logout")
async def logout(request: Request):
    token = request.cookies.get("access_token")
    if token:
        sign_out(token)
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie("access_token")
    response.delete_cookie("refresh_token")
    return response

@app.post("/repos/add")
async def add_repo(request: Request):
    user = require_auth(request)
    github_token = get_github_token(request)
    if not github_token:
        raise HTTPException(status_code=400, detail="GitHub token missing. Please log out and log in again.")

    body = await request.json()
    repo_full_name = body.get("repo", "").strip()
    if not repo_full_name or "/" not in repo_full_name:
        raise HTTPException(status_code=400, detail="Invalid repo format. Use owner/repo.")

    webhook_url = f"{os.environ.get('APP_URL')}/webhook"
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "")

    result = create_webhook(github_token, repo_full_name, webhook_url, secret)

    if "id" not in result:
        raise HTTPException(status_code=400, detail=result.get("message", "Failed to create webhook."))

    add_repository(user["id"], repo_full_name, result["id"])
    return {"ok": True, "webhook_id": result["id"]}


@app.get("/repos/list")
async def list_repos(request: Request):
    user = require_auth(request)
    return list_repositories(user["id"])


@app.post("/repos/delete")
async def remove_repo(request: Request):
    user = require_auth(request)
    github_token = get_github_token(request)

    body = await request.json()
    repo_full_name = body.get("repo", "").strip()

    row = delete_repository(user["id"], repo_full_name)
    if not row:
        raise HTTPException(status_code=404, detail="Repo not found.")

    if github_token and row.webhook_id:
        delete_webhook(github_token, repo_full_name, int(row.webhook_id))

    return {"ok": True}

@app.get("/auth/github/connect")
async def github_connect(request: Request):
    require_auth(request)
    url = (
        f"{SUPABASE_URL}/auth/v1/authorize"
        f"?provider=github"
        f"&scopes=repo,admin:repo_hook"
        f"&redirect_to={os.environ.get('APP_URL')}/auth/github/callback"
    )
    return RedirectResponse(url)


@app.get("/auth/github/callback")
async def github_callback(request: Request):
    """Dedicated callback for GitHub connect — always stores provider_token as github_token."""
    return templates.TemplateResponse("github_callback.html", {"request": request})


@app.post("/auth/github/store-token")
async def store_github_token(data: dict, response: Response):
    if not data.get("provider_token"):
        raise HTTPException(status_code=400, detail="No token provided.")
    response.set_cookie(
        "github_token", data["provider_token"],
        httponly=True, secure=True, samesite="lax", max_age=604800
    )
    return {"ok": True}


@app.get("/repos/github/list")
async def github_repo_list(request: Request):
    require_auth(request)
    github_token = request.cookies.get("github_token")
    if not github_token:
        raise HTTPException(status_code=401, detail="GitHub not connected.")

    response = requests.get(
        "https://api.github.com/user/repos?per_page=100&sort=updated&affiliation=owner,collaborator",
        headers={
            "Authorization": f"Bearer {github_token}",
            "Accept": "application/vnd.github+json"
        },
        timeout=10
    )
    repos = response.json()
    if not isinstance(repos, list):
        raise HTTPException(status_code=400, detail="Failed to fetch repos from GitHub.")

    return [{"full_name": r["full_name"], "private": r["private"]} for r in repos]


@app.get("/auth/github/status")
async def github_status(request: Request):
    require_auth(request)
    token = request.cookies.get("github_token")
    return {"connected": bool(token)}

@app.get("/terms")
async def terms(request: Request):
    return templates.TemplateResponse("terms.html", {"request": request})

@app.get("/privacy")
async def privacy(request: Request):
    return templates.TemplateResponse("privacy.html", {"request": request})

@app.get("/refund")
async def refund(request: Request):
    return templates.TemplateResponse("refund.html", {"request": request})

@app.get("/tickets")
async def tickets_list(request: Request):
    user = require_auth(request)
    tickets = get_tickets_by_user(user["id"])
    return templates.TemplateResponse("tickets/list.html", {
        "request": request,
        "user": user,
        "tickets": tickets
    })

@app.get("/tickets/new")
async def ticket_new(request: Request):
    user = require_auth(request)
    return templates.TemplateResponse("tickets/new.html", {
        "request": request,
        "user": user
    })

@app.post("/tickets/new")
async def ticket_create(
    request: Request,
    subject: str = Form(...),
    message: str = Form(...),
    images: List[UploadFile] = File(default=[])
):
    user = require_auth(request)
    ticket = create_ticket(user["id"], subject, message)
    if images:
        upload_ticket_images(str(ticket.id), images)
    return RedirectResponse(f"/tickets/{ticket.id}", status_code=303)

@app.get("/tickets/{ticket_id}")
async def ticket_detail(request: Request, ticket_id: str):
    user = require_auth(request)
    try:
        uuid.UUID(ticket_id)  # validate early
    except ValueError:
        raise HTTPException(status_code=404, detail="Ticket not found")
    ticket = get_ticket_by_id(ticket_id, user["id"])
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return templates.TemplateResponse("tickets/detail.html", {
        "request": request,
        "user": user,
        "ticket": ticket
    })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)