import os
import requests
from pathlib import Path
from dotenv import load_dotenv
from fastapi import Request, HTTPException

load_dotenv(dotenv_path=Path(__file__).parent.parent.parent / ".env")

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY")

if not SUPABASE_URL or not SUPABASE_ANON_KEY:
    raise ValueError("SUPABASE_URL and SUPABASE_ANON_KEY must be set.")

HEADERS = {
    "apikey": SUPABASE_ANON_KEY,
    "Content-Type": "application/json"
}


def sign_up(email: str, password: str) -> dict:
    """Register a new user with email and password."""
    response = requests.post(
        f"{SUPABASE_URL}/auth/v1/signup",
        headers=HEADERS,
        json={"email": email, "password": password},
        timeout=10
    )
    return response.json()


def sign_in(email: str, password: str) -> dict:
    """Sign in with email and password. Returns session tokens."""
    response = requests.post(
        f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
        headers=HEADERS,
        json={"email": email, "password": password},
        timeout=10
    )
    return response.json()


def get_oauth_url(provider: str) -> str:
    scopes = "repo,admin:repo_hook" if provider == "github" else ""
    scope_param = f"&scopes={scopes}" if scopes else ""
    return (
        f"{SUPABASE_URL}/auth/v1/authorize"
        f"?provider={provider}"
        f"{scope_param}"
        f"&redirect_to={os.environ.get('APP_URL', '')}/auth/callback"
    )


def get_user(access_token: str) -> dict | None:
    """Verifies an access token and returns the user."""
    response = requests.get(
        f"{SUPABASE_URL}/auth/v1/user",
        headers={
            **HEADERS,
            "Authorization": f"Bearer {access_token}"
        },
        timeout=10
    )
    if response.status_code == 200:
        data = response.json()
        return {
            "id": data.get("id"),
            "email": data.get("email"),
            "name": data.get("user_metadata", {}).get("full_name")
                 or data.get("user_metadata", {}).get("name")
                 or data.get("email"),
            "avatar": data.get("user_metadata", {}).get("avatar_url", ""),
        }
    return None


def refresh_session(refresh_token: str) -> dict:
    """Refreshes an expired access token."""
    response = requests.post(
        f"{SUPABASE_URL}/auth/v1/token?grant_type=refresh_token",
        headers=HEADERS,
        json={"refresh_token": refresh_token},
        timeout=10
    )
    return response.json()


def sign_out(access_token: str) -> bool:
    """Signs out the current user."""
    response = requests.post(
        f"{SUPABASE_URL}/auth/v1/logout",
        headers={
            **HEADERS,
            "Authorization": f"Bearer {access_token}"
        },
        timeout=10
    )
    return response.status_code == 204


def get_current_user(request: Request) -> dict | None:
    """Reads access token from cookie and returns current user."""
    token = request.cookies.get("access_token")
    if not token:
        return None
    return get_user(token)


def require_auth(request: Request) -> dict:
    """Protects routes — redirects to /login if not authenticated."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(
            status_code=307,
            headers={"Location": "/login"}
        )
    return user

def require_admin(request: Request) -> dict:
    """Protects admin routes — raises 403 if not admin."""
    user = require_auth(request)
    admin_email = os.environ.get("SITE_ADMIN_EMAIL")
    if user["email"] != admin_email:
        raise HTTPException(status_code=403, detail="Access denied")
    return user

def get_github_token(request: Request) -> str | None:
    return request.cookies.get("github_token")

def get_user_provider(access_token: str) -> str:
    response = requests.get(
        f"{SUPABASE_URL}/auth/v1/user",
        headers={**HEADERS, "Authorization": f"Bearer {access_token}"},
        timeout=10
    )
    print(f"DEBUG user identity response: {response.json()}")  # add this
    if response.status_code == 200:
        data = response.json()
        identities = data.get("identities", [])
        if identities:
            return identities[0].get("provider", "email")
    return "email"

if __name__ == "__main__":
    print("Auth module loaded successfully.")
    print(f"Supabase URL: {SUPABASE_URL}")
    print("Using direct HTTP API — no SDK dependency issues.")