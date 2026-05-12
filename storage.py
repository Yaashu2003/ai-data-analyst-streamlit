# storage.py
import os
import json
from pathlib import Path
from typing import Optional

# Dynamic path: project root (same folder as this script)
_BASE_DIR = Path(__file__).resolve().parent
SAVE_DIR = str(_BASE_DIR / "analysis_store")

def save_analysis(session_id: str, data: dict) -> str:
    os.makedirs(SAVE_DIR, exist_ok=True)
    file_path = os.path.join(SAVE_DIR, f"analysis_{session_id}.json")
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return file_path

def load_analysis(session_id: str) -> Optional[dict]:
    file_path = os.path.join(SAVE_DIR, f"analysis_{session_id}.json")
    if not os.path.exists(file_path):
        return None
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)
