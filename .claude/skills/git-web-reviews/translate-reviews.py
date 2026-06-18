#!/usr/bin/env python3
"""git-web-reviews スキルの本体スクリプト。

.git-web/reviews/<sha>.jsonl のレビューコメントを読み、resolved を畳み込み、
未解決コメントを target リビジョンの行へ翻訳して live / outdated / 追従不可 に
分類して出力する。

行翻訳ロジックは git-web 本体 packages/front/src/diff/translate-line.ts の
translateNewLine / translateRange を移植したもの。挙動は本体と一致させること。
本体側を更新したらこのファイルも追従する (二重保守。ADR 0060 / 0061 参照)。

依存ゼロ (Python 標準ライブラリのみ)。任意のリポジトリで動く。
使い方: python3 translate-reviews.py [from] [to]
"""

import json
import os
import re
import subprocess
import sys

SHA_FILE = re.compile(r"^([0-9a-f]{40})\.jsonl$")
HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
SHORT_LEN = 12


def git(args):
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True
    ).stdout


def git_ok(args):
    return subprocess.run(["git", *args], capture_output=True).returncode == 0


# --- 行翻訳 (translate-line.ts の移植) ---------------------------------------


def translate_new_line(old_line, hunks):
    """commentSHA 側 new 行番号を target 側へ翻訳する。

    返り値: ("mapped", line) または ("outdated", None)

    >>> translate_new_line(7, [])  # hunk が無ければそのまま
    ('mapped', 7)
    >>> h = {"old_start": 5, "old_lines": 1, "new_start": 5, "new_lines": 2,
    ...      "lines": ["add", "context"]}
    >>> translate_new_line(3, [h])  # hunk より前の行は不変
    ('mapped', 3)
    >>> h = {"old_start": 1, "old_lines": 3, "new_start": 1, "new_lines": 4,
    ...      "lines": ["context", "add", "context", "context"]}
    >>> translate_new_line(3, [h])  # 挿入で押し下げられた行はオフセット加算
    ('mapped', 4)
    >>> h = {"old_start": 1, "old_lines": 3, "new_start": 1, "new_lines": 2,
    ...      "lines": ["context", "delete", "context"]}
    >>> translate_new_line(2, [h])  # 削除された行は outdated
    ('outdated', None)
    """
    delta = 0
    for hunk in hunks:
        hunk_old_end = hunk["old_start"] + hunk["old_lines"] - 1
        if old_line < hunk["old_start"]:
            return ("mapped", old_line + delta)
        if old_line <= hunk_old_end:
            return _translate_inside_hunk(old_line, hunk)
        delta += hunk["new_lines"] - hunk["old_lines"]
    return ("mapped", old_line + delta)


def _translate_inside_hunk(old_line, hunk):
    old_cursor = hunk["old_start"]
    new_cursor = hunk["new_start"]
    for kind in hunk["lines"]:
        if kind == "context":
            if old_cursor == old_line:
                return ("mapped", new_cursor)
            old_cursor += 1
            new_cursor += 1
        elif kind == "delete":
            if old_cursor == old_line:
                return ("outdated", None)
            old_cursor += 1
        else:  # add: old 側を進めない
            new_cursor += 1
    return ("outdated", None)


def translate_range(start, end, hunks):
    """範囲翻訳。start が outdated なら範囲全体を outdated 扱い。

    返り値: ("mapped", start, end) または ("outdated", None, None)

    >>> h = {"old_start": 1, "old_lines": 3, "new_start": 1, "new_lines": 4,
    ...      "lines": ["context", "add", "context", "context"]}
    >>> translate_range(2, 3, [h])  # 2->3, 3->4
    ('mapped', 3, 4)
    >>> h = {"old_start": 1, "old_lines": 3, "new_start": 1, "new_lines": 2,
    ...      "lines": ["context", "delete", "context"]}
    >>> translate_range(2, 3, [h])  # start が削除されたら範囲全体 outdated
    ('outdated', None, None)
    """
    s_kind, s_line = translate_new_line(start, hunks)
    if s_kind == "outdated":
        return ("outdated", None, None)
    e_kind, e_line = translate_new_line(end, hunks)
    end_line = e_line if e_kind == "mapped" else s_line
    return ("mapped", s_line, end_line)


# --- unified diff -> hunks ---------------------------------------------------


def parse_hunks(diff_text):
    """unified diff テキストを hunk のリストへパースする。

    >>> diff = '''diff --git a/f b/f
    ... index 111..222 100644
    ... --- a/f
    ... +++ b/f
    ... @@ -1,3 +1,4 @@
    ...  a
    ... +x
    ...  b
    ...  c'''
    >>> hs = parse_hunks(diff)
    >>> len(hs)
    1
    >>> hs[0]["old_start"], hs[0]["old_lines"], hs[0]["new_lines"]
    (1, 3, 4)
    >>> hs[0]["lines"]
    ['context', 'add', 'context', 'context']
    >>> parse_hunks("")  # 差分なし (無変更ファイル)
    []
    """
    hunks = []
    cur = None
    for raw in diff_text.split("\n"):
        m = HUNK_HEADER.match(raw)
        if m:
            cur = {
                "old_start": int(m.group(1)),
                "old_lines": 1 if m.group(2) is None else int(m.group(2)),
                "new_start": int(m.group(3)),
                "new_lines": 1 if m.group(4) is None else int(m.group(4)),
                "lines": [],
            }
            hunks.append(cur)
            continue
        if cur is None:
            continue
        if raw.startswith("+"):
            cur["lines"].append("add")
        elif raw.startswith("-"):
            cur["lines"].append("delete")
        elif raw.startswith(" "):
            cur["lines"].append("context")
        # '\' (No newline at end of file) はスキップ
    return hunks


# --- reviews ファイル読み取り ------------------------------------------------


def read_comments(reviews_dir, sha):
    path = os.path.join(reviews_dir, f"{sha}.jsonl")
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip() == "":
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                sys.stderr.write(f"warn: skip broken comment line in {sha}.jsonl\n")
    return out


def read_resolved(reviews_dir, sha):
    """id ごとに resolved 状態を「append 出現順の後勝ち」で畳み込む。

    本体 packages/api/src/domain/review.ts の foldResolved と同一アルゴリズム
    (append 順に並んでいる前提で後に現れた値が勝つ。ts は比較に使わない)。
    本体を変えたらここも追従すること。
    """
    path = os.path.join(reviews_dir, f"{sha}.resolved.jsonl")
    result = {}  # id -> resolved: bool
    if not os.path.exists(path):
        return result
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip() == "":
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                sys.stderr.write(
                    f"warn: skip broken resolved line in {sha}.resolved.jsonl\n"
                )
                continue
            result[e.get("id")] = e.get("resolved") is True
    return result


# --- 対象 SHA 集合の決定 -----------------------------------------------------


def resolve_target_shas(reviews_dir, from_rev, to_rev):
    """返り値: (shas: list[str], empty_branch: bool)"""
    present = set()
    for name in os.listdir(reviews_dir):
        m = SHA_FILE.match(name)
        if m:
            present.add(m.group(1))

    if from_rev and to_rev:
        rng = [s for s in git(["rev-list", f"{from_rev}..{to_rev}"]).split("\n") if s]
        to_sha = git(["rev-parse", to_rev]).strip()
        candidates = set(rng) | {to_sha}
    else:
        base_branch = next(
            (b for b in ("main", "master")
             if git_ok(["rev-parse", "--verify", "--quiet", b])),
            None,
        )
        if base_branch:
            base = git(["merge-base", base_branch, "HEAD"]).strip()
            candidates = {
                s for s in git(["rev-list", f"{base}..HEAD"]).split("\n") if s
            }
            if not candidates:
                return ([], True)
        else:
            # ベースブランチ不明: 全 <sha>.jsonl をフォールバック対象に
            candidates = present

    return ([s for s in candidates if s in present], False)


# --- 分類 --------------------------------------------------------------------


def classify(comment, sha, target_sha, target_rev):
    """返り値: ("live", start, end) | ("outdated", None, None) | ("gone", None, None)"""
    path = comment["path"]
    if not git_ok(["cat-file", "-e", f"{target_sha}:{path}"]):
        return ("gone", None, None)
    if sha == target_sha:
        return ("live", comment["newLineStart"], comment["newLineEnd"])
    diff = git(["diff", f"{sha}..{target_rev}", "--", path])
    hunks = parse_hunks(diff)
    kind, start, end = translate_range(
        comment["newLineStart"], comment["newLineEnd"], hunks
    )
    if kind == "outdated":
        return ("outdated", None, None)
    return ("live", start, end)


# --- main --------------------------------------------------------------------


def main():
    args = sys.argv[1:]
    from_rev = args[0] if len(args) >= 1 else None
    to_rev = args[1] if len(args) >= 2 else None

    common_dir = git(
        ["rev-parse", "--path-format=absolute", "--git-common-dir"]
    ).strip()
    root = os.path.dirname(common_dir)
    reviews_dir = os.path.join(root, ".git-web", "reviews")
    if not os.path.isdir(reviews_dir):
        print("未解決コメントなし")
        return

    target_rev = to_rev if to_rev else "HEAD"
    target_sha = git(["rev-parse", target_rev]).strip()

    shas, empty_branch = resolve_target_shas(reviews_dir, from_rev, to_rev)
    if empty_branch or not shas:
        print("未解決コメントなし")
        return

    live, outdated, gone = [], [], []
    for sha in shas:
        resolved = read_resolved(reviews_dir, sha)
        for c in read_comments(reviews_dir, sha):
            if resolved.get(c.get("id")):
                continue
            kind, start, end = classify(c, sha, target_sha, target_rev)
            if kind == "live":
                live.append((c, sha, start, end))
            elif kind == "outdated":
                outdated.append((c, sha))
            else:
                gone.append((c, sha))

    if not live and not outdated and not gone:
        print("未解決コメントなし")
        return

    def short(s):
        return s[:SHORT_LEN]

    def orig(c):
        return f"L{c['newLineStart']}-{c['newLineEnd']}"

    out = []
    if live:
        out.append("## 未解決コメント (live)")
        for c, sha, start, end in live:
            out.append(f"- [{c['id']}] {c['path']} L{start}-{end} ← {short(sha)} {orig(c)}")
            out.append(f"  {c['body']}")
        out.append("")
    if outdated:
        out.append("## outdated (アンカー先が commentSHA..target で変更/削除済み — 参考)")
        for c, sha in outdated:
            out.append(f"- [{c['id']}] {c['path']} {short(sha)} {orig(c)} ※翻訳不能 (行が削除)")
            out.append(f"  {c['body']}")
        out.append("")
    if gone:
        out.append("## 追従不可 (target に path が存在しない / rename — 範囲外)")
        for c, sha in gone:
            out.append(f"- [{c['id']}] {c['path']} {short(sha)} {orig(c)} ※path 消失")
            out.append(f"  {c['body']}")
        out.append("")
    print("\n".join(out).rstrip())


if __name__ == "__main__":
    main()
