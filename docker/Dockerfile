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
    && dnf clean all

# Node.js 22のインストール
ENV NODE_VERSION=22.17.1
RUN curl -fsSL https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz \
    | tar -xJ -C /usr/local --strip-components=1

# 作業ディレクトリを設定
WORKDIR /app

# Claude Codeをグローバルインストール
RUN npm install -g @anthropic-ai/claude-code

# ubuntuユーザーの作成
RUN useradd -m -s /bin/bash ubuntu

# ヘルスチェック
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD claude --version && java -version

USER ubuntu

RUN mkdir /home/ubuntu/.ivy2
RUN mkdir /home/ubuntu/.config

# デフォルトコマンド
CMD ["claude"]
