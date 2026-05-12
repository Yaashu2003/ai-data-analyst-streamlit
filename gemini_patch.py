import os
import time
import asyncio

from google import genai
from google.genai.models import Models


PRIMARY_MODEL_PREFIX = "gemini-2"
FALLBACK_MODEL = "gemini-flash-latest"
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
    try:
        request = {
            "model": model,
            "contents": contents,
        }
        if config is not None:
            request["config"] = config
        return client.models.generate_content(**request)
    except Exception as error:
        if _should_fallback(model, str(error)):
            _safe_print(f"⚠️ {model} failed. Retrying chart analysis with {FALLBACK_MODEL}...")
            time.sleep(retry_delay)
            return _generate_with_v1_fallback(contents=contents, config=config, api_key=api_key)
        raise


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
