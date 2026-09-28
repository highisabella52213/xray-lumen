# Lumen V32: existing Python control plane/UI + pinned Xray data plane + Railway edge gateway.
FROM python:3.13-slim
ARG XRAY_VERSION=v26.9.9
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1 DATA_DIR=/data PORT=8080 LUMEN_CONTROL_PORT=8001 XRAY_BIN=/usr/local/bin/xray
WORKDIR /app
RUN apt-get update && apt-get install --no-install-recommends -y ca-certificates curl unzip tini gosu nodejs \
 && arch="$(dpkg --print-architecture)" \
 && case "$arch" in amd64) asset=Xray-linux-64.zip;; arm64) asset=Xray-linux-arm64-v8a.zip;; *) echo "unsupported architecture: $arch"; exit 1;; esac \
 && curl -fL --retry 3 "https://github.com/XTLS/Xray-core/releases/download/${XRAY_VERSION}/${asset}" -o /tmp/xray.zip \
 && unzip -q /tmp/xray.zip xray -d /usr/local/bin \
 && chmod 0755 /usr/local/bin/xray \
 && /usr/local/bin/xray version \
 && rm -f /tmp/xray.zip \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --gid 10001 lumen && useradd --uid 10001 --gid lumen --create-home --home-dir /app lumen \
 && install -d --owner=lumen --group=lumen --mode=0700 /data
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY --chown=lumen:lumen . /app
RUN chmod 0755 /app/docker-entrypoint.sh /app/edge_gateway.js
EXPOSE 8080
ENTRYPOINT ["/usr/bin/tini","--","/app/docker-entrypoint.sh"]
CMD ["v32"]
