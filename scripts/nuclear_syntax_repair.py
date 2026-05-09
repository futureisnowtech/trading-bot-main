import os
import re
import subprocess

def check_file(path):
    try:
        subprocess.run(['python3', '-m', 'py_compile', path], check=True, capture_output=True)
        return True, ""
    except subprocess.CalledProcessError as e:
        return False, e.stderr.decode()

def repair_file(path):
    with open(path, 'r') as f:
        content = f.read()
    
    # 1. Fix malformed from config import (...)
    content = re.sub(r'from\s+config\s+import\s*\(\s*([^)]*?)(?:,\s*)?(?:False|True)(?:,\s*)?([^)]*?)\)', r'from config import (\1, \2)', content)
    # Cleanup empty imports or excessive commas
    content = content.replace('(, ', '(').replace(', )', ')').replace(', ,', ',')
    
    # 2. Fix broken SQL bindings (the specific ones found so far)
    content = re.sub(r'WHERE\s+\)\.fetchall\(\)', 'WHERE paper=0").fetchall()', content)
    
    # 3. Fix broken con.execute nesting
    content = content.replace('con.execute(\n        con.execute(', 'con.execute(')
    
    # 4. Remove standalone "False," or "True," lines that were probably imports
    lines = content.splitlines()
    new_lines = []
    for line in lines:
        if line.strip() in ("False,", "True,"):
            continue
        new_lines.append(line)
    content = "\n".join(new_lines)

    with open(path, 'w') as f:
        f.write(content)

# Find all .py files
for root, dirs, files in os.walk('.'):
    for file in files:
        if file.endswith('.py') and not root.startswith('./.'):
            path = os.path.join(root, file)
            ok, _ = check_file(path)
            if not ok:
                print(f"Repairing {path}...")
                repair_file(path)
                # Verify again
                ok, err = check_file(path)
                if not ok:
                    print(f"STILL BROKEN: {path}\n{err}")
                else:
                    print(f"FIXED: {path}")

