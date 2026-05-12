import os
import zipfile
import shutil


def extract_all_reports(report_folder):

    extracted_paths = {}

    for file in os.listdir(report_folder):

        if not file.endswith(".pbix"):
            continue

        pbix_path = os.path.join(report_folder, file)
        name = os.path.splitext(file)[0]

        extract_folder = os.path.join(report_folder, f"{name}_extracted")

        # -----------------------------
        # CLEAN OLD EXTRACTION
        # -----------------------------
        if os.path.exists(extract_folder):
            shutil.rmtree(extract_folder)

        os.makedirs(extract_folder, exist_ok=True)

        print(f"[Processing] {file}")

        # -----------------------------
        # UNZIP DIRECTLY (FIXED)
        # -----------------------------
        try:
            with zipfile.ZipFile(pbix_path, 'r') as zip_ref:
                zip_ref.extractall(extract_folder)

        except zipfile.BadZipFile:
            print(f"[ERROR] {file} is not a valid PBIX/ZIP")
            continue

        # -----------------------------
        # CHECK LAYOUT
        # -----------------------------
        layout_path = os.path.join(extract_folder, "Report", "Layout")

        if os.path.exists(layout_path):
            print(f"[OK] Layout found: {layout_path}")
            extracted_paths[name] = layout_path
        else:
            print(f"[WARN] Layout not found for {name}")

    print(f"[Done] Total reports processed: {len(extracted_paths)}")

    return extracted_paths