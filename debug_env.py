import os
from dotenv import load_dotenv

# Try to load .env
loaded = load_dotenv()
print(f"DEBUG: .env loaded: {loaded}")

# Get key
key = os.getenv("GEMINI_API_KEY")
print(f"DEBUG: GEMINI_API_KEY: {key}")

if key:
    print(f"DEBUG: Key starts with: {key[:8]}... and ends with: ...{key[-4:]}")
else:
    print("DEBUG: GEMINI_API_KEY is NOT set in environment.")
