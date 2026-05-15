import os
import re

versions_dir = "api/app/migrations/versions"
revisions = {}
down_revisions = set()

for filename in os.listdir(versions_dir):
    if filename.endswith(".py") and not filename.startswith("__"):
        path = os.path.join(versions_dir, filename)
        with open(path) as f:
            content = f.read()
            rev_match = re.search(r'revision:?\s*(?:str\s*)?=\s*["\']([^"\']+)["\']', content)
            down_rev_match = re.search(r'down_revision:?\s*(?:str\s*(?:\|\s*tuple\[str,\s*\.\.\.\])?\s*\|\s*None)?\s*=\s*(?:["\']([^"\']+)["\']|\(([^)]+)\)|None)', content)
            
            if rev_match:
                rev = rev_match.group(1)
                revisions[rev] = filename
                if down_rev_match:
                    if down_rev_match.group(1):
                        down_revisions.add(down_rev_match.group(1))
                    elif down_rev_match.group(2):
                        # Handle tuple of down_revisions
                        parts = [p.strip().strip('"').strip("'") for p in down_rev_match.group(2).split(",")]
                        for p in parts:
                            if p:
                                down_revisions.add(p)

heads = set(revisions.keys()) - down_revisions
print(f"Heads: {heads}")
