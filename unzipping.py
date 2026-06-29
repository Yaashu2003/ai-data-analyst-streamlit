import os
import zipfile
import shutil
import xml.etree.ElementTree as ET


def extract_all_reports(report_folder):

    extracted_paths = {}

    for file in os.listdir(report_folder):

        ext = os.path.splitext(file)[1].lower()
        name = os.path.splitext(file)[0]
        full_path = os.path.join(report_folder, file)

        # -----------------------------
        # POWER BI (.pbix)
        # -----------------------------
        if ext == ".pbix":
            extract_folder = os.path.join(report_folder, f"{name}_extracted")

            if os.path.exists(extract_folder):
                shutil.rmtree(extract_folder)
            os.makedirs(extract_folder, exist_ok=True)

            print(f"[Processing PBIX] {file}")

            try:
                with zipfile.ZipFile(full_path, 'r') as zip_ref:
                    zip_ref.extractall(extract_folder)
            except zipfile.BadZipFile:
                print(f"[ERROR] {file} is not a valid PBIX/ZIP")
                continue

            layout_path = os.path.join(extract_folder, "Report", "Layout")

            if os.path.exists(layout_path):
                print(f"[OK] Layout found: {layout_path}")
                extracted_paths[name] = layout_path
            else:
                print(f"[WARN] Layout not found for {name}")

        # -----------------------------
        # TABLEAU (.twbx — packaged)
        # -----------------------------
        elif ext == ".twbx":
            extract_folder = os.path.join(report_folder, f"{name}_twbx_extracted")

            if os.path.exists(extract_folder):
                shutil.rmtree(extract_folder)
            os.makedirs(extract_folder, exist_ok=True)

            print(f"[Processing TWBX] {file}")

            try:
                with zipfile.ZipFile(full_path, 'r') as zip_ref:
                    zip_ref.extractall(extract_folder)

                twb_files = [
                    os.path.join(extract_folder, f)
                    for f in os.listdir(extract_folder)
                    if f.endswith(".twb")
                ]
                if twb_files:
                    twb_path = twb_files[0]
                    extracted_paths[name] = ("tableau", twb_path)
                    print(f"[OK] Tableau workbook found inside TWBX: {twb_path}")
                else:
                    print(f"[WARN] No .twb found inside {file}")
            except zipfile.BadZipFile:
                print(f"[ERROR] {file} is not a valid TWBX/ZIP")
                continue

        # -----------------------------
        # TABLEAU (.twb — plain XML)
        # -----------------------------
        elif ext == ".twb":
            print(f"[Processing TWB] {file}")
            extracted_paths[name] = ("tableau", full_path)

    print(f"[Done] Total reports processed: {len(extracted_paths)}")

    return extracted_paths