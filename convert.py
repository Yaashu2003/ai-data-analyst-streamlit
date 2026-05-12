import os
import yaml

def folder_to_yaml(folder_path, output_filename):
    code_dict = {}
    
    for root, _, files in os.walk(folder_path):
        for file in files:
            file_path = os.path.join(root, file)
            
            # This makes the keys in your YAML relative to the data_analyst-hitl0002 base
            rel_path = os.path.relpath(file_path, folder_path)
            
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    code_dict[rel_path] = f.read()
            except UnicodeDecodeError:
                print(f"Skipping binary/unreadable file: {rel_path}")

    with open(output_filename, 'w', encoding='utf-8') as f:
        yaml.dump(code_dict, f, default_flow_style=False)
        
    print(f"Success! YAML saved to: {output_filename}")

# Your exact Windows paths
target_folder = r"C:\Users\Swarna\Desktop\NVIDIA_agenticAI_\data_analyst-hitl0002"

# Saving the output YAML one level up so it doesn't get caught inside its own folder
output_file = r"C:\Users\Swarna\Desktop\NVIDIA_agenticAI_\agent_codebase.yaml"

folder_to_yaml(target_folder, output_file)