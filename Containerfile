# MCP Ashigaru CrunchTools Container
#
# The dev-ops backbone for the CrunchTools fleet. On lotor it runs as a
# ROOT-managed system container on the `crunchtools` network. The unprivileged
# `devrunner` user's rootless podman socket is bind-mounted in (CONTAINER_HOST),
# so the Python runner launches sealed Claude Code workers rootless as devrunner.
#
# All operations (git, podman, gh) are now native Python subprocess calls —
# no bash wrapper scripts. UBI-minimal provides the shell for subprocess.
#
# Build (via GHA -> quay; never hand-pushed):
#   podman build -t quay.io/crunchtools/mcp-ashigaru .
#
# Run (see the mcp-ashigaru.crunchtools.com.service systemd unit on lotor):
#   podman run -d --network crunchtools -p 127.0.0.1:8020:8020 \
#     --user 1000:1000 \
#     -e CONTAINER_HOST=unix:///run/podman/podman.sock \
#     -v /run/user/1000/podman/podman.sock:/run/podman/podman.sock \
#     -v /home/devrunner/ashigaru:/home/devrunner/ashigaru:z \
#     --env-file /srv/mcp-ashigaru.crunchtools.com/config/mcp-ashigaru.env \
#     quay.io/crunchtools/mcp-ashigaru \
#     --transport streamable-http --host 0.0.0.0 --port 8020

FROM registry.access.redhat.com/ubi10/ubi-minimal:latest

LABEL name="mcp-ashigaru-crunchtools" \
      version="1.0.0" \
      summary="CrunchTools dev-ops backbone — features from ticket to production" \
      description="Ashigaru: drives features from ticket to production across all agents (Josui, Kagetora)" \
      maintainer="crunchtools.com" \
      url="https://github.com/crunchtools/mcp-ashigaru" \
      io.k8s.display-name="MCP Ashigaru CrunchTools" \
      io.openshift.tags="mcp,agents,claude-code,devops" \
      org.opencontainers.image.source="https://github.com/crunchtools/mcp-ashigaru" \
      org.opencontainers.image.description="CrunchTools dev-ops backbone — features from ticket to production" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later"

# Runtime tooling: python, git, gh, podman client, curl (for healthchecks).
# gh comes from the official GitHub CLI dnf repo (arch-correct, GPG-verified).
RUN curl -fsSL -o /etc/yum.repos.d/gh-cli.repo https://cli.github.com/packages/rpm/gh-cli.repo && \
    microdnf install -y python3 python3-pip python3-devel gcc-c++ cmake make \
        git podman-remote gh ca-certificates && \
    microdnf clean all && \
    ln -sf /usr/bin/podman-remote /usr/local/bin/podman

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN python3 -m pip install --no-cache-dir . && \
    python3 -c "from mcp_ashigaru import main; print('Installation verified')"

RUN mkdir -p /home/devrunner && chown 1000:1000 /home/devrunner
ENV HOME=/home/devrunner \
    ASHIGARU_STATE_DIR=/home/devrunner/ashigaru \
    PYTHONUNBUFFERED=1
USER 1000

EXPOSE 8020
ENTRYPOINT ["python3", "-m", "mcp_ashigaru"]
