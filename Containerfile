# MCP Ashigaru CrunchTools Container
#
# The bridge between Kagetora and the dev-runner sandbox. On lotor it runs like
# every other MCP server: a ROOT-managed system container on the `crunchtools`
# network. The unprivileged `devrunner` user's rootless podman socket is bind
# -mounted in (CONTAINER_HOST), so the wrapper scripts launch the *workers*
# (Claude Code) rootless as devrunner — only the workers run rootless.
#
# Base image: ubi10/ubi-minimal (NOT Hummingbird distroless) — evaluated in
# issue #2. The wrapper scripts require bash, git, gh, jq, and podman-remote.
# Both Hummingbird Python variants are worse for this runtime role:
#
#   hummingbird/python:latest         — distroless, no shell; scripts cannot run.
#   hummingbird/python:latest-builder — ships the full build toolchain; larger
#                                        image and higher CVE surface than
#                                        ubi-minimal used as a pure runtime.
#
# ubi-minimal + microdnf installs exactly the runtime tools needed and nothing
# more, and receives dnf CVE patches. It remains the correct base here.
# Shell-form RUN is fine because ubi-minimal ships /bin/sh.
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
      version="0.2.0" \
      summary="MCP bridge dispatching Claude Code as headless dev sub-agents" \
      description="Kagetora's Ashigaru corps: drive Claude Code to fix issues and open PRs, in an unprivileged sandbox" \
      maintainer="crunchtools.com" \
      url="https://github.com/crunchtools/mcp-ashigaru" \
      io.k8s.display-name="MCP Ashigaru CrunchTools" \
      io.openshift.tags="mcp,agents,claude-code,devops" \
      org.opencontainers.image.source="https://github.com/crunchtools/mcp-ashigaru" \
      org.opencontainers.image.description="MCP bridge dispatching Claude Code as headless dev sub-agents" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later"

# Runtime tooling for the wrapper scripts: shell, python, git, jq, podman client.
# gh comes from the official GitHub CLI dnf repo (arch-correct, GPG-verified).
RUN curl -fsSL -o /etc/yum.repos.d/gh-cli.repo https://cli.github.com/packages/rpm/gh-cli.repo && \
    microdnf install -y python3 python3-pip git jq podman-remote gh ca-certificates && \
    microdnf clean all && \
    ln -sf /usr/bin/podman-remote /usr/local/bin/podman

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY runner/ /app/runner/

RUN python3 -m pip install --no-cache-dir . && \
    python3 -c "from mcp_ashigaru import main; print('Installation verified')" && \
    chmod +x /app/runner/*.sh

# Run as devrunner's uid so files written to the shared state volume (and the
# bind-mounted rootless socket) are owned by devrunner (1000) host-side.
RUN mkdir -p /home/devrunner && chown 1000:1000 /home/devrunner
ENV HOME=/home/devrunner \
    ASHIGARU_RUNNER_DIR=/app/runner \
    ASHIGARU_STATE_DIR=/home/devrunner/ashigaru
USER 1000

EXPOSE 8020
ENTRYPOINT ["python3", "-m", "mcp_ashigaru"]
