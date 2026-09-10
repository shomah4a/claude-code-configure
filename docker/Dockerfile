FROM ubuntu:26.04

# 旧ベースイメージ (amazoncorretto:21-al2023) が設定していた ENV を引き継ぐ。
# JAVA_HOME のパスはアーキテクチャ名を含む。Node.js の取得も linux-x64 固定のため amd64 前提とする。
ENV LANG=C.UTF-8
ENV JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64

# apt の対話プロンプト (tzdata 等) を抑止する。ビルド時のみ有効にするため ARG にする
ARG DEBIAN_FRONTEND=noninteractive

# システムパッケージの更新と必要なツールのインストール
# `which` は仮想パッケージのため実体の gnu-which を指定する
RUN apt-get update && \
    apt-get -y upgrade && \
    apt-get install -y --no-install-recommends \
    openjdk-21-jdk \
    git \
    python3 \
    python3-pip \
    python3-venv \
    build-essential \
    tar \
    xz-utils \
    jq \
    procps \
    poppler-utils \
    curl \
    ca-certificates \
    wget \
    gnu-which \
    emacs-nox \
    rcs \
    unzip \
    less \
    && rm -rf /var/lib/apt/lists/*

# Node.js 22のインストール
ENV NODE_VERSION=22.17.1
RUN curl -fsSL https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz \
    | tar -xJ -C /usr/local --strip-components=1

# Claude Codeをグローバルインストール
RUN npm install -g @anthropic-ai/claude-code@2.1.259

# Codex をグローバルインストール
RUN npm install -g @openai/codex@0.144.1

# AWS CLI v2 のインストール
# 公式配布の zip をバージョン固定で取得し、同梱の install スクリプトで /usr/local/aws-cli に展開する
# (/usr/local/bin/aws に symlink が作られる)。
# apt の awscli は Ubuntu 26.04 では 2.31.35 (universe) だが、バージョンを固定できずディストリ版に追随するため使わない。
# unzip は展開に、less は AWS CLI v2 の既定 pager として必要 (上の apt で導入)。groff は入れていないため aws help は利用不可。
# バージョン付き URL の形式: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-version.html
# amd64 固定 (aarch64 は awscli-exe-linux-aarch64-<version>.zip)。root で実行するため USER ubuntu より前に置く。
ENV AWS_CLI_VERSION=2.36.42
RUN curl -fsSL https://awscli.amazonaws.com/awscli-exe-linux-x86_64-${AWS_CLI_VERSION}.zip -o /tmp/awscliv2.zip \
    && unzip -oq /tmp/awscliv2.zip -d /tmp \
    && /tmp/aws/install \
    && rm -rf /tmp/aws /tmp/awscliv2.zip

# 作業ディレクトリを設定
WORKDIR /app

# Claude Code LSP Plugin
# RUN claude plugins add jdtls-lsp@claude-plugins-official

# ubuntuユーザーの作成
# ホスト側ユーザーとファイル所有者を揃えるため uid/gid は 1000 に固定する。
# Ubuntu 24.04 以降のベースイメージには uid 1000 の ubuntu ユーザーが既に存在するため、
# 存在する場合は uid/gid とシェルを明示的に揃え、存在しない場合のみ作成する。
# 最後にビルド時アサーションを行い、期待と異なればビルドを失敗させる。
ENV CONTAINER_UID=1000
ENV CONTAINER_GID=1000
RUN set -eu; \
    if id ubuntu >/dev/null 2>&1; then \
        groupmod -g ${CONTAINER_GID} ubuntu; \
        usermod -u ${CONTAINER_UID} -g ${CONTAINER_GID} -s /bin/bash ubuntu; \
    else \
        groupadd -g ${CONTAINER_GID} ubuntu; \
        useradd -m -u ${CONTAINER_UID} -g ${CONTAINER_GID} -s /bin/bash ubuntu; \
    fi; \
    test "$(id -u ubuntu)" = "${CONTAINER_UID}"; \
    test "$(id -g ubuntu)" = "${CONTAINER_GID}"; \
    test "$(getent passwd ubuntu | cut -d: -f7)" = "/bin/bash"; \
    install -d -o ubuntu -g ubuntu \
        /home/ubuntu \
        /home/ubuntu/.ivy2 \
        /home/ubuntu/.config \
        /home/ubuntu/bin

# ヘルスチェック
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD claude --version && java -version

USER ubuntu

RUN cat <<'EOF' > /home/ubuntu/.bashrc
PATH=$PATH:$HOME/bin
alias myclaude="claude --permission-mode auto"
alias mycodex="codex -s danger-full-access -a on-request"
EOF


# デフォルトコマンド
CMD ["claude"]
