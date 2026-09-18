#!/usr/bin/env bash
# Placement AI agent installer.
#
# Usage: curl -fsSL https://app.placement-ai.com/install.sh | bash
#
# This installs the OpenAgents SDK/CLI, which is the agent runtime Placement AI
# builds on — a real upstream dependency, not a leftover brand reference, so
# the source stays openagents.org. Only the URL a Placement AI user is told to
# curl belongs on a Placement AI host.

exec bash <(curl -fsSL https://openagents.org/install.sh)
