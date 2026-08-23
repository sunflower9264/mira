FROM node:22-bookworm-slim AS node_runtime

FROM python:3.12-slim

ARG CODEX_VERSION=0.147.0
ARG RUNTIME_DEFINITION_SHA256=unversioned

ENV DEBIAN_FRONTEND=noninteractive

COPY --from=node_runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node_runtime /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        chromium \
        curl \
        fonts-noto-cjk \
        git \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g @openai/codex@${CODEX_VERSION}

LABEL mira.runtime.definition-sha256="${RUNTIME_DEFINITION_SHA256}" \
    mira.runtime.codex-version="${CODEX_VERSION}"

RUN useradd --create-home --shell /bin/bash --uid 1000 mira

WORKDIR /workspace

COPY runtime/scripts/capture_screenshots.py /opt/mira/capture_screenshots.py
RUN chmod 0755 /opt/mira/capture_screenshots.py \
    && chown -R mira:mira /opt/mira

USER mira

ENV HOME=/home/mira \
    USERPROFILE=/home/mira \
    CODEX_HOME=/home/mira \
    PATH=/home/mira/.local/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin
