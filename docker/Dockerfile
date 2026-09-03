FROM public.ecr.aws/amazoncorretto/amazoncorretto:21-al2023

# システムパッケージの更新と必要なツールのインストール
RUN dnf update -y && \
    dnf install -y \
    git \
    python3 \
    python3-pip \
    gcc \
    gcc-c++ \
    make \
    tar \
    xz \
    shadow-utils \
    jq \
    procps-ng \
    poppler-utils \
    wget \
    which \
    emacs-nox \
    && dnf clean all

# Node.js 22のインストール
ENV NODE_VERSION=22.17.1
RUN curl -fsSL https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz \
    | tar -xJ -C /usr/local --strip-components=1

# Claude Codeをグローバルインストール
RUN npm install -g @anthropic-ai/claude-code@2.1.259

# Codex をグローバルインストール
RUN npm install -g @openai/codex@0.144.1

# 作業ディレクトリを設定
WORKDIR /app

# Claude Code LSP Plugin
# RUN claude plugins add jdtls-lsp@claude-plugins-official

# ubuntuユーザーの作成
RUN useradd -m -s /bin/bash ubuntu

# ヘルスチェック
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD claude --version && java -version

USER ubuntu


RUN mkdir /home/ubuntu/.ivy2
RUN mkdir /home/ubuntu/.config
RUN mkdir /home/ubuntu/bin

RUN cat <<'EOF' > /home/ubuntu/.bashrc
PATH=$PATH:$HOME/bin
alias myclaude="claude --permission-mode auto"
alias mycodex="codex -s danger-full-access -a on-request"
EOF


# デフォルトコマンド
CMD ["claude"]
