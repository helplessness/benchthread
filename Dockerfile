FROM node:22-bookworm-slim AS web-builder

WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --registry=https://registry.npmjs.org
ARG TCODE_INSTALL_LARK_CLI=1
RUN mkdir -p /opt/lark-cli \
    && if [ "$TCODE_INSTALL_LARK_CLI" = 1 ]; then \
        apt-get update \
        && apt-get install -y --no-install-recommends ca-certificates curl \
        && rm -rf /var/lib/apt/lists/* \
        && npm install -g @larksuite/cli@1.0.98 --registry=https://registry.npmjs.org \
        && install -m 0755 /usr/local/lib/node_modules/@larksuite/cli/bin/lark-cli /opt/lark-cli/lark-cli; \
    fi
COPY web/ ./
# The Web type check also covers tests that import this checked-in scene fixture.
COPY docs/IntelligentUI/examples/guided-bearing.json /src/docs/IntelligentUI/examples/
# Rollup's production graph exceeds Node's default container heap.
RUN NODE_OPTIONS=--max-old-space-size=4096 npm run build

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TCODE_CONFIG_ROOT=/config \
    TCODE_WORKDIR=/workspace \
    TCODE_ENTERPRISE_MODE=1

WORKDIR /app
COPY requirements.lock ./
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
RUN python -m pip install --no-cache-dir --require-hashes -r requirements.lock

COPY pyproject.toml MANIFEST.in README.md LICENSE VERSION ./
COPY tcode_agent/ ./tcode_agent/
COPY --from=web-builder /src/web/dist/ ./tcode_agent/server/static/
COPY --from=web-builder /opt/lark-cli/ /usr/local/bin/
RUN python -m pip install --no-cache-dir --no-deps .

RUN useradd --create-home --uid 10001 tcode && mkdir -p /config /workspace
COPY config/examples/local-demo/ /config/
RUN chown -R tcode:tcode /config /workspace

EXPOSE 8080
VOLUME ["/workspace", "/config"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=3)" || exit 1

USER tcode
CMD ["tcode-server", "--host", "0.0.0.0", "--port", "8080", "--workdir", "/workspace"]
