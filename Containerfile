# MCP Ashigaru CrunchTools Container
# Built on Hummingbird Python image (Red Hat UBI-based) for enterprise security.
#
# The bridge between Kagetora and the dev-runner sandbox. At runtime on lotor it
# runs under the unprivileged `devrunner` user with that user's rootless podman
# socket mounted, so it can launch the agent containers and run the gate wrappers.
#
# Build:
#   podman build -t quay.io/crunchtools/mcp-ashigaru .
#
# Run (streamable-http on the crunchtools network for the airlock gateway):
#   podman run -d -p 127.0.0.1:8020:8020 quay.io/crunchtools/mcp-ashigaru \
#     --transport streamable-http --host 0.0.0.0 --port 8020

FROM quay.io/hummingbird/python:latest

LABEL name="mcp-ashigaru-crunchtools" \
      version="0.1.0" \
      summary="MCP bridge dispatching Claude Code as headless dev sub-agents" \
      description="Kagetora's Ashigaru corps: drive Claude Code to fix issues and open PRs, in an unprivileged sandbox" \
      maintainer="crunchtools.com" \
      url="https://github.com/crunchtools/mcp-ashigaru" \
      io.k8s.display-name="MCP Ashigaru CrunchTools" \
      io.openshift.tags="mcp,agents,claude-code,devops" \
      org.opencontainers.image.source="https://github.com/crunchtools/mcp-ashigaru" \
      org.opencontainers.image.description="MCP bridge dispatching Claude Code as headless dev sub-agents" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later"

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN pip install --no-cache-dir .

RUN python -c "from mcp_ashigaru import main; print('Installation verified')"

EXPOSE 8020
ENTRYPOINT ["python", "-m", "mcp_ashigaru"]
