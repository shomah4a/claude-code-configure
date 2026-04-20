---
name: dev-activity-report
description: Claude Codeを利用した開発活動の評価レポートを生成する。コミット履歴とセッションログを分析し、対話パターン・関与スタイル・改善提案を出力する。
---

# 開発活動評価レポート生成

以下の手順でレポートを生成すること。

## Step 0: 対象期間を決定する

スキル引数は最大2つ受け取る。いずれも `YYYY-MM-DD` 形式の UTC 日付として解釈する。

- 引数なし: 直近14日 (`since = 現在日 − 14日`、`until = 現在日`)
- 引数1つ (`<since>`): `since` から現在日まで
- 引数2つ (`<since> <until>`): 指定レンジ（両端包含）

引数のフォーマットが不正な場合、または `since > until` の場合はレポート生成を中止してユーザーに指摘すること。

以降のステップでは、確定した `<since>` と `<until>` を使用する。

## Step 1: プロジェクトのセッションディレクトリを特定する

`~/.claude/projects/` 以下から、現在の作業ディレクトリに対応するプロジェクトディレクトリを特定する。
作業ディレクトリのパスから `/` を `-` に置換し、先頭に `-` を付けたものがディレクトリ名になる。

例: `/home/shoma/work/test/editor-test` → `-home-shoma-work-test-editor-test`

## Step 2: セッションログからユーザーメッセージを抽出する

以下のスクリプトを実行してセッションログの分析結果を取得する:

```bash
python3 ${CLAUDE_SKILL_DIR}/extract-session-messages.py \
    ~/.claude/projects/<プロジェクトディレクトリ>/ \
    --since <since> --until <until>
```

引数なしで呼ばれた場合はスクリプト側のデフォルト (直近14日) が適用されるため、`--since/--until` を省略しても可。
ただしレポートの一貫性のため、Step 0 で決定した日付を明示的に渡すことを推奨する。

出力はJSON形式で以下を含む:
- `date_range`: 適用された対象期間 (`since`, `until` いずれも `YYYY-MM-DD`)
- `total_messages`: 期間内の総メッセージ数
- `total_sessions`: 期間内にメッセージがあったセッション数
- `context_overflows`: コンテキスト溢れ回数
- `categories`: メッセージの分類別カウント
- `engagement_style`: 関与スタイル判定結果（スコアと内訳）
- `samples`: カテゴリごとのメッセージサンプル

## Step 3: コミット履歴を取得する

期間指定を git log に反映する。
`git log --until` は exclusive で解釈されるため、`<until>` の当日を包含するには翌日を渡すこと。
シェル上では `until_exclusive=$(date -u -d "<until> + 1 day" +%Y-%m-%d)` のように求める。

```bash
# 総コミット数
git log --oneline --all --since=<since> --until=<until_exclusive> | wc -l

# コミット一覧
git log --oneline --all --since=<since> --until=<until_exclusive>

# 日別コミット数
git log --format='%ai' --all --since=<since> --until=<until_exclusive> | awk '{print $1}' | sort | uniq -c | sort -k2

# 総変更量
git log --all --shortstat --format='' --since=<since> --until=<until_exclusive> | awk '/files changed/ {f+=$1; a+=$4; d+=$6} END {print "Files:", f, "Add:", a, "Del:", d}'
```

## Step 4: レポートを生成する

以下の構成でマークダウンレポートを生成し、`.claude/tmp/YYYY-MM-DD_development-activity-report.md` に書き出す。
ファイル名の `YYYY-MM-DD` は生成日（現在日）を使用する。

### レポート構成

#### 1. 概要
- プロジェクト名
- 対象期間 (`<since>` 〜 `<until>`。Step 2 の `date_range` と一致させること)
- コミット数、変更量、セッション数

#### 2. 関与スタイル判定
Step 2 の `engagement_style` の結果を元に、ユーザーの関与スタイルを判定する。

スタイルの定義:
- **pair-pro-driver（ペアプロ ドライバー）**: 実装の細部まで介入し、割り込みや却下が多い。コードレベルの設計判断を自ら行う
- **pair-pro-navigator（ペアプロ ナビゲーター）**: 設計方針を示し議論をリードするが、実装はClaude Codeに委ねる。質問と議論が多い
- **pdm（プロダクトマネージャー）**: 機能要求と承認が中心。実装の詳細には関与せず「何を作るか」を指示する

スコアだけでなく、`rates`（各カテゴリの出現率）と`samples`（実際の発言例）を照合して、判定の根拠を具体的な発言とともに記述すること。

#### 3. 対話パターン分析
セッションログのサンプルメッセージを引用しながら、以下を分析する:

- **効果的なパターン**: 品質向上に寄与している対話の特徴
- **改善余地のあるパターン**: 効率や品質を改善できる対話の特徴
- **問題のあるパターン**: リスクや非効率を生んでいる対話の特徴

各パターンには `samples` から具体的な発言を引用すること。

#### 4. Claude Code側の問題への対処
`rejection` カテゴリや `interrupt` カテゴリのメッセージから、Claude Codeが起こした問題とユーザーの対処を抽出する。

#### 5. セッション管理の評価
- コンテキスト溢れの頻度
- セッションの切り替えタイミングの適切さ
- セッションサイズの分布

#### 6. 改善提案
上記の分析に基づく具体的な改善提案を記述する。
提案はユーザーの対話スタイルの改善と、`.claude/rules/` へのルール追加候補の両方を含むこと。

#### 7. まとめ
観点ごとの評価をテーブル形式でまとめる。

### レポート生成時の注意

- 事実と推測は明確に区別すること
- サンプルメッセージの引用は原文のまま行うこと
- 定量データ（カテゴリ別カウント、出現率）を根拠として示すこと
- 改善提案は具体的かつ実行可能なものに限ること
- 対象期間はレポート全体で一貫させること（Step 0 で確定したレンジから外れるデータを引用しないこと）
