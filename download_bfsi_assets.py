import os
import urllib.request
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DOWNLOADS_DIR = BASE_DIR / "downloads"
DOWNLOADS_DIR.mkdir(exist_ok=True)

print(f"Target downloads folder: {DOWNLOADS_DIR}")

# Assets to download: (filename, urls_to_try)
ASSETS = [
    (
        "bank_loan_dashboard.pbix",
        [
            "https://github.com/Pratik94229/Bank-Loan-Dashboard---Power-BI/raw/main/bank%20loan%20dashboard.pbix",
            "https://github.com/Pratik94229/Bank-Loan-Dashboard---Power-BI/raw/master/bank%20loan%20dashboard.pbix",
        ]
    ),
    (
        "Bank_Loan_Performance_Dashboard.twbx",
        [
            "https://github.com/vinayjdc/Bank-Loan-Performance-Dashboard/raw/main/Bank.twbx",
            "https://github.com/vinayjdc/Bank-Loan-Performance-Dashboard/raw/master/Bank.twbx",
        ]
    ),
    (
        "Bank_Loan_Project.twbx",
        [
            "https://github.com/Sankari0299/Bank-Loan-Project-----Tableau/raw/main/Bank%20Loan%20Project.twbx",
            "https://github.com/Sankari0299/Bank-Loan-Project-----Tableau/raw/master/Bank%20Loan%20Project.twbx",
        ]
    ),
    (
        "bank_churn_dataset.csv",
        [
            "https://raw.githubusercontent.com/selva86/datasets/master/Churn_Modelling.csv",
        ]
    ),
    (
        "financial_loan.csv",
        [
            "https://raw.githubusercontent.com/vbhatsaccnt/Bank-Loan-Data-Analysis/main/financial_loan.csv",
        ]
    )
]

def download_file(filename, urls):
    dest_path = DOWNLOADS_DIR / filename
    print(f"\nDownloading {filename}...")
    
    for url in urls:
        print(f"Trying URL: {url}")
        try:
            req = urllib.request.Request(
                url, 
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            )
            with urllib.request.urlopen(req, timeout=20) as response:
                content = response.read()
                dest_path.write_bytes(content)
                print(f"Successfully downloaded and saved: {filename} ({len(content)} bytes)")
                return True
        except Exception as e:
            print(f"Failed to download from {url}. Error: {e}")
            
    print(f"Could not download {filename} from any of the provided URLs.")
    return False

success_count = 0
for filename, urls in ASSETS:
    if download_file(filename, urls):
        success_count += 1

print(f"\nDownload process finished. Successfully downloaded {success_count}/{len(ASSETS)} assets.")
if success_count > 0:
    print("Downloaded files can be found in:")
    print(DOWNLOADS_DIR)
else:
    sys.exit(1)
