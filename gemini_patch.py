import os
import time
import asyncio

from google import genai
from google.genai.models import Models


PRIMARY_MODEL_PREFIX = "gemini-"
FALLBACK_CHAIN = [
    "gemini-3.1-flash-lite",   # free tier primary
    "gemini-3-flash",           # free tier secondary
    "gemini-2.5-flash",         # legacy fallback
    "gemini-flash-latest",      # last resort alias
]
FALLBACK_MODEL = FALLBACK_CHAIN[0]
STREAMLIT_SECRET_KEYS = (
    "GEMINI_API_KEY",
    "GROQ_API_KEY",
    "API_KEY",
    "OPENAI_API_KEY",
    "API_BASE_URL",
)

# Save the original generate_content function
original_generate_content = Models.generate_content


def _bootstrap_streamlit_secrets_into_env() -> None:
    """
    Mirror Streamlit Cloud secrets into environment variables early enough for
    modules that still rely on os.getenv(...) at import time.
    """
    try:
        import streamlit as st

        secrets = getattr(st, "secrets", None)
        if not secrets:
            return

        for key in STREAMLIT_SECRET_KEYS:
            value = secrets.get(key)
            if value and not os.getenv(key):
                os.environ[key] = str(value)
    except Exception:
        # Streamlit is optional outside the deployed app.
        return


def _safe_print(message: str) -> None:
    """Print safely on Windows terminals that cannot encode emoji."""
    try:
        print(message)
    except UnicodeEncodeError:
        sanitized = (
            message.replace("⚠️", "[WARN]")
            .replace("✅", "[OK]")
            .encode("ascii", errors="ignore")
            .decode("ascii")
        )
        print(sanitized)


_bootstrap_streamlit_secrets_into_env()


def _should_fallback(model: str, error_message: str) -> bool:
    normalized = (error_message or "").upper()
    return model.startswith(PRIMARY_MODEL_PREFIX) and any(
        token in normalized
        for token in ("429", "503", "LIMIT", "RESOURCE_EXHAUSTED", "UNAVAILABLE")
    )


def _generate_with_v1_fallback(contents, config=None, api_key=None):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            asyncio.set_event_loop(asyncio.new_event_loop())
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    key = api_key or os.getenv("GEMINI_API_KEY")
    fallback_client = genai.Client(api_key=key, http_options={"api_version": "v1beta"})

    request = {
        "model": FALLBACK_MODEL,
        "contents": contents,
    }
    if config is not None:
        request["config"] = config

    return fallback_client.models.generate_content(**request)


def generate_content_with_model_fallback(
    client,
    model,
    contents,
    config=None,
    api_key=None,
    retry_delay=2,
):
    # Build the chain: try requested model first, then walk FALLBACK_CHAIN
    tried = [model]
    chain = [m for m in FALLBACK_CHAIN if m != model] + []

    def _try_model(m, use_v1beta=False):
        key = api_key or os.getenv("GEMINI_API_KEY")
        if use_v1beta:
            c = genai.Client(api_key=key, http_options={"api_version": "v1beta"})
        else:
            c = client
        request = {"model": m, "contents": contents}
        if config is not None:
            request["config"] = config
        return c.models.generate_content(**request)

    # Try primary model
    try:
        return _try_model(model)
    except Exception as error:
        err_msg = str(error).upper()
        is_quota = any(t in err_msg for t in ("429", "503", "LIMIT", "RESOURCE_EXHAUSTED", "UNAVAILABLE", "NOT_FOUND", "404"))
        if not is_quota:
            raise

    # Walk fallback chain
    for fallback in chain:
        _safe_print(f"⚠️  {tried[-1]} failed → trying {fallback} ...")
        tried.append(fallback)
        time.sleep(retry_delay)
        try:
            return _try_model(fallback, use_v1beta=(fallback == "gemini-flash-latest"))
        except Exception as error:
            err_msg = str(error).upper()
            is_quota = any(t in err_msg for t in ("429", "503", "LIMIT", "RESOURCE_EXHAUSTED", "UNAVAILABLE", "NOT_FOUND", "404"))
            if not is_quota:
                raise
            continue

    raise RuntimeError(f"All Gemini models exhausted: {tried}")




def generate_content_with_fallback(self, *args, **kwargs):
    """
    A global monkey-patch for Google GenAI Models.generate_content.
    If a gemini-2.x model fails with a rate limit or limit: 0 error,
    it automatically falls back to gemini-flash-latest.
    """
    model = kwargs.get("model", "")
    if not model and args:
        model = args[0]

    try:
        return original_generate_content(self, *args, **kwargs)
    except Exception as error:
        err_msg = str(error)
        if _should_fallback(model, err_msg):
            _safe_print(f"⚠️ {model} failed. Global patch retrying with {FALLBACK_MODEL} via Gemini API v1...")
            time.sleep(2)

            contents = kwargs.get("contents")
            if contents is None and len(args) > 1:
                contents = args[1]
            config = kwargs.get("config")
            return _generate_with_v1_fallback(contents=contents, config=config)

        raise


# Apply the monkey-patch
Models.generate_content = generate_content_with_fallback
_safe_print("✅ Global Gemini Fallback Patch Applied.")
