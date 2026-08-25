FROM node:22-bookworm-slim AS node_runtime

FROM python:3.12-slim

ARG CODEX_VERSION=0.147.0
ARG PLAYWRIGHT_CLI_VERSION=0.1.18
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

RUN npm install -g @openai/codex@${CODEX_VERSION} \
    && PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm install -g @playwright/cli@${PLAYWRIGHT_CLI_VERSION} \
    && test "$(playwright-cli --version)" = "${PLAYWRIGHT_CLI_VERSION}" \
    && test -x /usr/bin/chromium

RUN pip install --no-cache-dir 'markitdown[pdf,docx,pptx,xls,xlsx,outlook]==0.1.7'

LABEL mira.runtime.definition-sha256="${RUNTIME_DEFINITION_SHA256}" \
    mira.runtime.codex-version="${CODEX_VERSION}" \
    mira.runtime.playwright-cli-version="${PLAYWRIGHT_CLI_VERSION}"

RUN useradd --create-home --shell /bin/bash --uid 1000 mira

WORKDIR /workspace

COPY runtime/scripts/capture_screenshots.py /opt/mira/capture_screenshots.py
COPY runtime/scripts/mira-browser /opt/mira/mira-browser
COPY runtime/scripts/convert_to_markdown.py /opt/mira/convert_to_markdown.py
COPY runtime/playwright-cli.config.json /opt/mira/playwright-cli.config.json
RUN chmod 0755 /opt/mira/capture_screenshots.py /opt/mira/mira-browser /opt/mira/convert_to_markdown.py \
    && ln -s /opt/mira/mira-browser /usr/local/bin/mira-browser \
    && chown -R mira:mira /opt/mira

USER mira

ENV HOME=/home/mira \
    USERPROFILE=/home/mira \
    CODEX_HOME=/home/mira \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PATH=/home/mira/.local/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin
