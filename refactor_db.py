import os
import re

def remove_conn_close(directory):
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith(".py"):
                path = os.path.join(root, file)
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                
                # We want to remove lines that are exactly `conn.close()` with optional whitespace
                new_content = re.sub(r'^[ \t]*conn\.close\(\)\s*\n', '', content, flags=re.MULTILINE)
                
                if new_content != content:
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(new_content)
                    print(f"Updated {path}")

if __name__ == "__main__":
    app_dir = os.path.join(os.path.dirname(__file__), 'app')
    remove_conn_close(app_dir)
    print("Done removing conn.close()")
