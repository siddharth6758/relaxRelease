import time
import sys


def retry(func, retries: int = 3, delay: int = 5, backoff: int = 2):
    """
    Retries a function on failure with exponential backoff.

    retries : number of attempts
    delay   : initial wait in seconds
    backoff : multiplier applied to delay after each failure
    """
    attempt = 0
    current_delay = delay

    while attempt < retries:
        try:
            return func()
        except RuntimeError as e:
            attempt += 1
            if attempt == retries:
                print(f"❌ All {retries} attempts failed.")
                print(f"   Last error: {e}")
                sys.exit(1)
            print(f"⚠️  Attempt {attempt} failed: {e}")
            print(f"   Retrying in {current_delay} seconds...")
            time.sleep(current_delay)
            current_delay *= backoff


def handle_rate_limit(response_status: int, response_text: str, service: str):
    """
    Raises a clear RuntimeError for rate limit and auth errors.
    """
    if response_status == 429:
        raise RuntimeError(f"{service} rate limit hit. Will retry.")
    if response_status == 401:
        print(f"❌ {service} authentication failed. Check your API key.")
        sys.exit(1)
    if response_status == 403:
        print(f"❌ {service} permission denied. Check token scopes.")
        sys.exit(1)
    if response_status >= 500:
        raise RuntimeError(f"{service} server error {response_status}. Will retry.")
    raise RuntimeError(f"{service} error {response_status}: {response_text}")