#!/usr/bin/env python3
"""セッションログ(JSONL)からユーザーメッセージを抽出し、分析用サマリーを出力する。

Usage:
    python3 extract-session-messages.py <project-session-dir> [--since YYYY-MM-DD] [--until YYYY-MM-DD]

期間指定:
    --since / --until はいずれも UTC 日付として解釈する。
    --since 省略時は 現在日 - 14日
    --until 省略時は 現在日
    境界は両端包含 (since の 00:00:00 UTC から until の 23:59:59.999 UTC まで)

出力: 各セッションのユーザーメッセージ一覧と統計情報をstdoutに出力する。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path


DEFAULT_WINDOW_DAYS = 14


def parse_date(s: str) -> datetime:
    """YYYY-MM-DD を UTC の 00:00:00 の datetime に変換する。"""
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def parse_timestamp(ts: str) -> datetime | None:
    """JSONL の timestamp フィールド (ISO 8601, Z 終端想定) を datetime に変換する。"""
    if not ts:
        return None
    try:
        # "2026-04-20T14:47:57.148Z" → fromisoformat は Z を解釈できないため置換
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def extract_user_messages(jsonl_path: str, since: datetime, until_exclusive: datetime) -> list[str]:
    """JSONLファイルからユーザーメッセージを抽出する。期間外のメッセージは除外する。"""
    messages = []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            if obj.get("userType") != "external" or obj.get("type") != "user":
                continue

            ts = parse_timestamp(obj.get("timestamp", ""))
            if ts is None:
                # timestamp が欠損しているレコードは期間判定不能なためスキップ
                continue
            if ts < since or ts >= until_exclusive:
                continue

            msg = obj.get("message", {})
            if not isinstance(msg, dict):
                continue

            content = msg.get("content", "")
            if isinstance(content, list):
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "text":
                        text = c["text"].strip()
                        if text:
                            messages.append(text)
            elif isinstance(content, str) and content.strip():
                messages.append(content.strip())

    return messages


def classify_message(msg: str) -> str:
    """メッセージを分類する。"""
    lower = msg.lower()

    if msg.startswith("[Request interrupted"):
        return "interrupt"
    if msg.startswith("This session is being continued"):
        return "context_overflow_continuation"
    if msg.startswith("<local-command-caveat>") or msg.startswith("<bash-"):
        return "local_command"

    short_approvals = [
        "ok", "go", "はい", "yes", "いいです", "します", "しましょう",
        "どうぞ", "よさそう", "大丈夫そう", "いいすね", "いいっすね",
        "まあいいかな", "よし", "一旦よし",
    ]
    if any(msg.strip().rstrip("。、") == a for a in short_approvals):
        return "short_approval"

    if "マージ" in msg or "コミット" in msg:
        return "git_operation"

    if "?" in msg or "？" in msg or "ですか" in msg or "ですかね" in msg or "でしょうか" in msg:
        return "question"

    if any(w in msg for w in ["だめ", "いらん", "禁止", "してはいけない", "使わない", "やめ"]):
        return "rejection"

    if any(w in msg for w in ["ほしい", "したい", "作りたい", "やりたい", "欲しい"]):
        return "feature_request"

    if any(w in msg for w in ["動かない", "だめそう", "おかしい", "壊れ", "効かない", "エラー", "バグ"]):
        return "bug_report"

    if any(w in msg for w in ["次は", "次のステップ", "続き"]):
        return "next_step"

    return "discussion"


def detect_engagement_style(categories: Counter, total: int) -> dict:
    """ユーザーの関与スタイルを判定する。"""
    if total == 0:
        return {"style": "unknown", "confidence": 0.0, "breakdown": {}}

    approval_rate = categories.get("short_approval", 0) / total
    question_rate = categories.get("question", 0) / total
    rejection_rate = categories.get("rejection", 0) / total
    interrupt_rate = categories.get("interrupt", 0) / total
    discussion_rate = categories.get("discussion", 0) / total
    feature_rate = categories.get("feature_request", 0) / total
    bug_rate = categories.get("bug_report", 0) / total

    # スコアリング
    # ドライバー: 割り込み多い、却下多い、議論多い、承認少ない
    driver_score = (
        interrupt_rate * 2.0
        + rejection_rate * 2.0
        + discussion_rate * 1.0
        + bug_rate * 1.5
        - approval_rate * 0.5
    )

    # ナビゲーター: 質問多い、議論多い、機能要求あり、適度な承認
    navigator_score = (
        question_rate * 2.0
        + discussion_rate * 1.5
        + feature_rate * 1.0
        + approval_rate * 0.3
    )

    # PdM: 承認多い、機能要求多い、割り込み少ない、議論少ない
    pdm_score = (
        approval_rate * 2.0
        + feature_rate * 1.5
        - interrupt_rate * 1.0
        - discussion_rate * 0.5
    )

    scores = {
        "pair-pro-driver": driver_score,
        "pair-pro-navigator": navigator_score,
        "pdm": pdm_score,
    }

    style = max(scores, key=scores.get)
    max_score = scores[style]
    total_score = sum(abs(v) for v in scores.values())
    confidence = max_score / total_score if total_score > 0 else 0.0

    return {
        "style": style,
        "confidence": round(confidence, 2),
        "scores": {k: round(v, 3) for k, v in scores.items()},
        "rates": {
            "approval": round(approval_rate, 3),
            "question": round(question_rate, 3),
            "rejection": round(rejection_rate, 3),
            "interrupt": round(interrupt_rate, 3),
            "discussion": round(discussion_rate, 3),
            "feature_request": round(feature_rate, 3),
            "bug_report": round(bug_rate, 3),
        },
    }


def resolve_date_range(since_arg: str | None, until_arg: str | None, now: datetime) -> tuple[datetime, datetime]:
    """CLI引数から期間を決定する。戻り値は (since_inclusive, until_exclusive)。"""
    today_utc_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)

    if since_arg:
        since = parse_date(since_arg)
    else:
        since = today_utc_midnight - timedelta(days=DEFAULT_WINDOW_DAYS)

    if until_arg:
        until_inclusive_day = parse_date(until_arg)
    else:
        until_inclusive_day = today_utc_midnight

    # until の日を包含するため翌日 00:00 を半開区間の上端として返す
    until_exclusive = until_inclusive_day + timedelta(days=1)
    return since, until_exclusive


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("session_dir", help="プロジェクトのセッションディレクトリ")
    parser.add_argument("--since", help="開始日 YYYY-MM-DD (UTC, inclusive)。省略時は現在日-14日")
    parser.add_argument("--until", help="終了日 YYYY-MM-DD (UTC, inclusive)。省略時は現在日")
    args = parser.parse_args()

    session_dir = Path(args.session_dir)
    if not session_dir.is_dir():
        print(f"Error: {session_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    now = datetime.now(tz=timezone.utc)
    try:
        since, until_exclusive = resolve_date_range(args.since, args.until, now)
    except ValueError as e:
        print(f"Error: 日付のパースに失敗しました: {e}", file=sys.stderr)
        sys.exit(1)

    if since >= until_exclusive:
        print(f"Error: --since は --until より前の日付を指定してください", file=sys.stderr)
        sys.exit(1)

    jsonl_files = sorted(session_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)

    if not jsonl_files:
        print(f"Error: No .jsonl files found in {session_dir}", file=sys.stderr)
        sys.exit(1)

    all_messages = []
    all_categories = Counter()
    session_summaries = []

    for jsonl_path in jsonl_files:
        messages = extract_user_messages(str(jsonl_path), since, until_exclusive)
        if not messages:
            continue

        categories = Counter()
        for msg in messages:
            cat = classify_message(msg)
            categories[cat] += 1

        all_messages.extend(messages)
        all_categories += categories

        session_summaries.append({
            "file": jsonl_path.name,
            "size_mb": round(jsonl_path.stat().st_size / 1024 / 1024, 1),
            "message_count": len(messages),
            "categories": dict(categories),
        })

    total = len(all_messages)
    engagement = detect_engagement_style(all_categories, total)

    # ユーザーメッセージのサンプル抽出（カテゴリごとに最大5件）
    samples = {}
    for msg in all_messages:
        cat = classify_message(msg)
        if cat not in samples:
            samples[cat] = []
        if len(samples[cat]) < 5:
            # 長すぎるメッセージは切り詰める
            samples[cat].append(msg[:200])

    until_inclusive_display = (until_exclusive - timedelta(days=1)).strftime("%Y-%m-%d")
    output = {
        "date_range": {
            "since": since.strftime("%Y-%m-%d"),
            "until": until_inclusive_display,
        },
        "total_messages": total,
        "total_sessions": len(session_summaries),
        "context_overflows": all_categories.get("context_overflow_continuation", 0),
        "categories": dict(all_categories),
        "engagement_style": engagement,
        "sessions": session_summaries,
        "samples": samples,
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
