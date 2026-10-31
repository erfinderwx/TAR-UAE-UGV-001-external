"""Check the sample's metadata, stable document names and relative links."""
import json
import hashlib
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

import yaml

ROOT = Path(__file__).resolve().parents[1]
ERRORS = []
checked = 0
for path in sorted(ROOT.rglob('*.md')):
    rel = path.relative_to(ROOT)
    if '.git' in rel.parts:
        continue
    text = path.read_text(encoding='utf-8')
    governed = rel.parts[0] == 'docs' and '_templates' not in rel.parts and path.name not in ['README.md', 'INDEX.md']
    if governed:
        checked += 1
        match = re.match(r'^---\s*\n(.*?)\n---\s*\n', text, re.S)
        if not match:
            ERRORS.append(f'{rel}: missing front matter')
        else:
            try:
                fields = yaml.safe_load(match.group(1))
                if not isinstance(fields, dict):
                    raise ValueError('front matter is not a mapping')
                for key in ['title', 'owner', 'status', 'version', 'updated', 'summary']:
                    if not fields.get(key):
                        ERRORS.append(f'{rel}: missing {key}')
                if fields.get('status') not in ['草稿', '评审中', '定稿', '作废']:
                    ERRORS.append(f'{rel}: invalid status')
            except Exception as exc:
                ERRORS.append(f'{rel}: invalid metadata: {exc}')
        if re.search(r'\s|:|[Vv]\d+\.\d+|最终版', path.stem):
            ERRORS.append(f'{rel}: unstable document name')
    # Code examples describe names that need not exist; they are not active links.
    body = re.sub(r'^```[^\n]*\n.*?^```\s*$', '', text, flags=re.M | re.S)
    body = re.sub(r'`[^`\n]*`', '', body)
    for target in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)', body):
        target = target.strip().split(' "', 1)[0].strip('<>')
        if not target or target.startswith('#'):
            continue
        parts = urlsplit(target)
        if parts.scheme in ['http', 'https', 'mailto']:
            continue
        if parts.scheme or target.startswith('/'):
            ERRORS.append(f'{rel}: nonportable link {target}')
            continue
        resolved = (path.parent / unquote(parts.path)).resolve()
        try:
            resolved.relative_to(ROOT)
        except ValueError:
            ERRORS.append(f'{rel}: link leaves repository: {target}')
            continue
        if not resolved.exists():
            ERRORS.append(f'{rel}: missing link {target}')
for path in ROOT.rglob('*'):
    if path.is_file() and '.git' not in path.relative_to(ROOT).parts and path.stat().st_size > 50_000_000:
        ERRORS.append(f'{path.relative_to(ROOT)}: exceeds the sample 50 MB limit')
manifest_path = ROOT / 'sources/records/source-manifest.json'
if manifest_path.exists():
    for item in json.loads(manifest_path.read_text(encoding='utf-8'))['files']:
        original = (ROOT / item['path']).resolve()
        if not original.is_relative_to(ROOT) or not original.is_file():
            ERRORS.append(f"Missing archived original: {item['path']}")
        elif hashlib.sha256(original.read_bytes()).hexdigest() != item['sha256']:
            ERRORS.append(f"Archived original checksum differs: {item['path']}")
report = {'documents': checked, 'errors': ERRORS}
print(json.dumps(report, ensure_ascii=False, indent=2))
sys.exit(bool(ERRORS))
