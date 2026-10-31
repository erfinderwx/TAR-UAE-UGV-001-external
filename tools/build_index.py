#!/usr/bin/env python3
"""扫描 docs/ 下所有 Markdown 文档，读取文件头（front matter），
生成文档总览看板，写进 README.md 的标记区和 docs/INDEX.md。

用法：python3 tools/build_index.py          # 生成
      python3 tools/build_index.py --check  # 只检查文件头，不写文件（CI 用）
"""
import os, re, subprocess, sys, datetime
from zoneinfo import ZoneInfo
from collections import Counter  # noqa

try:
    import yaml
except ImportError:
    sys.exit("缺少 PyYAML：pip install pyyaml")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")
README = os.path.join(ROOT, "README.md")
INDEX = os.path.join(DOCS, "INDEX.md")
START, END = "<!-- INDEX:START -->", "<!-- INDEX:END -->"

STATUSES = ["草稿", "评审中", "定稿", "作废"]
REQUIRED = ["title", "owner", "status", "version", "updated", "summary"]
SKIP_DIRS = {"_templates"}
SKIP_FILES = {"INDEX.md", "README.md"}


def front_matter(path):
    text = open(path, encoding="utf-8").read()
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.S)
    if not m:
        return None
    try:
        data = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        return {"__error__": str(e)}
    return data if isinstance(data, dict) else None


def infer_from_body(path):
    """没有文件头的文档：从正文推断标题、版本、日期（只读，不改原文）。"""
    head = open(path, encoding="utf-8").read().split("\n")[:20]
    title = next((l[2:].strip() for l in head if l.startswith("# ")), "")
    text = "\n".join(head)
    # 版本优先级：H1 标题里的版本 → "版本：" 那一行的第一个版本号 → 开头 20 行里最大的版本号
    pat = r"v(\d+(?:\.\d+)+)"
    m = re.search(pat, title)
    if not m:
        m = next((re.search(pat, l) for l in head if "版本" in l and re.search(pat, l)), None)
    if m:
        version = "v" + m.group(1)
    else:
        vers = re.findall(pat, text)
        version = ("v" + max(vers, key=lambda v: [int(x) for x in v.split(".")])) if vers else ""
    dates = re.findall(r"20\d\d-\d\d-\d\d", text)
    updated = max(dates) if dates else ""
    return {"title": title, "version": version, "updated": updated}


LINK = re.compile(r"\]\(([^)\s]+)\)")


def missing_links(path):
    """文档里指向本库、但库里还没有的文件。"""
    out = []
    text = open(path, encoding="utf-8").read()
    for t in LINK.findall(text):
        if re.match(r"^(https?:|mailto:|#)", t):
            continue
        t = t.split("#")[0]
        if not t:
            continue
        from urllib.parse import unquote
        target = os.path.normpath(os.path.join(os.path.dirname(path), unquote(t)))
        if not os.path.exists(target):
            out.append(os.path.relpath(target, ROOT).replace(os.sep, "/"))
    return out


def git_last(path):
    """最后一次提交的日期和作者；没有提交记录时返回空。"""
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%cs|%aN", "--", path],
            cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        return tuple(out.split("|", 1)) if out else ("", "")
    except Exception:
        return ("", "")


def collect():
    docs, problems, notes, missing = [], [], [], {}
    for dp, dn, fn in os.walk(DOCS):
        dn[:] = sorted(d for d in dn if d not in SKIP_DIRS and not d.startswith("."))
        for f in sorted(fn):
            if not f.endswith(".md") or f in SKIP_FILES:
                continue
            path = os.path.join(dp, f)
            rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
            fm = front_matter(path)
            inferred = False
            if fm is None:
                notes.append(f"{rel}：没有文件头，标题/版本/日期按正文推断，状态为「未标注」")
                fm = infer_from_body(path)
                inferred = True
            elif "__error__" in fm:
                problems.append(f"{rel}：文件头格式错误 {fm['__error__']}")
                fm = {}
            else:
                for k in REQUIRED:
                    if not fm.get(k):
                        problems.append(f"{rel}：文件头缺少 {k}")
            for m in missing_links(path):
                missing.setdefault(m, []).append(rel)
            st = str(fm.get("status", "")).strip()
            if st and st not in STATUSES:
                problems.append(f"{rel}：status 应为 {' / '.join(STATUSES)} 之一，现在是「{st}」")
            gdate, gauthor = git_last(path)
            section = os.path.relpath(dp, DOCS).replace(os.sep, "/")
            docs.append({
                "rel": rel, "section": "（根目录）" if section == "." else section,
                "title": str(fm.get("title") or os.path.splitext(f)[0]),
                "owner": str(fm.get("owner", "")), "status": st or "未标注",
                "version": str(fm.get("version", "")),
                "updated": str(fm.get("updated", "")),
                "summary": str(fm.get("summary", "")).replace("\n", " ").replace("|", "／"),
                "tags": fm.get("tags") or [],
                "git_date": gdate, "git_author": gauthor, "inferred": inferred,
            })
    return docs, problems, notes, missing


def link(d, base):
    target = os.path.relpath(os.path.join(ROOT, d["rel"]), base).replace(os.sep, "/")
    return f"[{d['title']}]({target.replace(' ', '%20')})"


def render(docs, base):
    lines = []
    today = datetime.datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    cnt = Counter(d["status"] for d in docs)
    lines.append(f"> 自动生成于 {today}，共 **{len(docs)}** 篇。请不要手动修改这一段。没有文件头的文档，版本和日期取自正文开头，标为「推断」。\n")
    if docs:
        # 最近更新
        recent = sorted(docs, key=lambda d: (d["git_date"] or d["updated"]), reverse=True)[:8]
        lines.append("### 最近更新\n")
        lines.append("| 文档 | 状态 | 版本 | 最后提交 | 提交人 |")
        lines.append("|---|---|---|---|---|")
        for d in recent:
            lines.append(f"| {link(d, base)} | {d['status']} | {d['version']} | {d['git_date'] or '未提交'} | {d['git_author']} |")
        lines.append("")
        # 按目录分组的全表
        lines.append("### 全部文档\n")
        by_sec = {}
        for d in docs:
            by_sec.setdefault(d["section"], []).append(d)
        for sec in sorted(by_sec):
            lines.append(f"**{sec}**\n")
            lines.append("| 文档 | 负责人 | 状态 | 版本 | 文件头日期 | 摘要 |")
            lines.append("|---|---|---|---|---|---|")
            for d in sorted(by_sec[sec], key=lambda x: x["rel"]):
                ver = d['version'] + ("（推断）" if d.get("inferred") and d['version'] else "")
                lines.append(f"| {link(d, base)} | {d['owner']} | {d['status']} | {ver} | {d['updated']} | {d['summary']} |")
            lines.append("")
    else:
        lines.append("（还没有文档。复制 `docs/_templates/文档模板.md` 开始写第一篇。）\n")
    return "\n".join(lines)


def main():
    check_only = "--check" in sys.argv
    docs, problems, notes, missing = collect()
    for p in problems:
        print("⚠️ ", p)
    for n in notes:
        print("ℹ️ ", n)
    if check_only:
        print(f"检查完成：{len(docs)} 篇文档，{len(problems)} 个问题。")
        sys.exit(1 if problems else 0)

    # README 标记区
    readme = open(README, encoding="utf-8").read()
    block = render(docs, ROOT)
    if START in readme and END in readme:
        readme = re.sub(re.escape(START) + r".*?" + re.escape(END),
                        lambda _: f"{START}\n{block}\n{END}", readme, flags=re.S)
        open(README, "w", encoding="utf-8").write(readme)
    # docs/INDEX.md
    idx = "# 文档总览\n\n" + render(docs, DOCS)
    if problems:
        idx += "\n### 文件头有问题\n\n" + "\n".join(f"- {p}" for p in problems) + "\n"
    if missing:
        idx += "\n### 被引用、但库里还没有的文件\n\n> 这些文件在文档里有链接，但还没上传。上传后链接自动生效。\n\n"
        idx += "| 缺少的文件 | 被哪些文档引用 |\n|---|---|\n"
        for m in sorted(missing):
            refs = "、".join(sorted({os.path.basename(r) for r in missing[m]}))
            idx += f"| `{m}` | {refs} |\n"
    if notes:
        idx += "\n### 没有文件头的文档\n\n" + "\n".join(f"- {n}" for n in notes) + "\n"
    open(INDEX, "w", encoding="utf-8").write(idx)
    print(f"已更新总览：{len(docs)} 篇文档，{len(problems)} 个文件头问题，{len(missing)} 个缺失引用。")


if __name__ == "__main__":
    main()
