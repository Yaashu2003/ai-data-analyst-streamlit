import shutil
from pathlib import Path
import zipfile

BASE_DIR = Path(__file__).resolve().parent

REPORT_FILE = BASE_DIR / "interactive_analysis_report.html"
CHARTS_DIR = BASE_DIR / "charts_html"

OUTPUT_ZIP = BASE_DIR / "final_report.zip"


def create_report_zip():
    try:
        if not REPORT_FILE.exists():
            print("❌ Report missing")
            return

        if not CHARTS_DIR.exists():
            print("❌ charts_html missing")
            return

        with zipfile.ZipFile(OUTPUT_ZIP, 'w', zipfile.ZIP_DEFLATED) as zipf:

            # add report
            zipf.write(REPORT_FILE, REPORT_FILE.name)

            # add charts folder
            for file in CHARTS_DIR.rglob("*"):
                zipf.write(file, file.relative_to(BASE_DIR))

        print("✅ ZIP created:", OUTPUT_ZIP)

    except Exception as e:
        print("❌ Error:", e)


if __name__ == "__main__":
    create_report_zip()