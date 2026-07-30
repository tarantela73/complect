FROM debian:bookworm-slim AS downloader

ARG SUPERMEMORY_SERVER_VERSION=0.0.6
ARG TARGETARCH

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

RUN --mount=type=cache,target=/var/cache/supermemory,sharing=locked \
    case "${TARGETARCH}" in \
        amd64) \
            platform="x64"; \
            checksum="bb1b7cee393818236873b8e2518a435e10d9195e27ea5608a3af48a733ef8ee8" \
            ;; \
        arm64) \
            platform="arm64"; \
            checksum="68ff5a95e9c7c2b701254262c0bf680c0ebe3603d45e84524331da9c40055fc6" \
            ;; \
        *) \
            echo "Unsupported architecture: ${TARGETARCH}" >&2; \
            exit 1 \
            ;; \
    esac \
    && download_path="/var/cache/supermemory/supermemory-server-linux-${platform}-${SUPERMEMORY_SERVER_VERSION}" \
    && curl --fail --show-error --location \
        --continue-at - \
        --connect-timeout 30 \
        --retry 5 \
        --retry-all-errors \
        --retry-delay 2 \
        --speed-limit 1024 \
        --speed-time 60 \
        "https://github.com/supermemoryai/supermemory/releases/download/server-v${SUPERMEMORY_SERVER_VERSION}/supermemory-server-linux-${platform}" \
        --output "${download_path}" \
    && echo "${checksum}  ${download_path}" | sha256sum --check --strict \
    && cp "${download_path}" /tmp/supermemory-server \
    && chmod 0755 /tmp/supermemory-server

FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates curl \
    && groupadd --gid 10001 supermemory \
    && useradd --uid 10001 --gid 10001 --home-dir /data --no-create-home \
        --shell /usr/sbin/nologin supermemory \
    && rm -rf /var/lib/apt/lists/*

COPY --from=downloader /tmp/supermemory-server /usr/local/bin/supermemory-server

ENV HOME=/data \
    PORT=6767 \
    SUPERMEMORY_DATA_DIR=/data

USER 10001:10001

EXPOSE 6767

ENTRYPOINT ["/usr/local/bin/supermemory-server"]
