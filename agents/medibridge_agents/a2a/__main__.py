"""Run one agent service: ``python -m medibridge_agents.a2a <agent_id>`` (loopback only).

``python -m medibridge_agents.a2a --list`` prints each agent and its port.
"""
from __future__ import annotations

import argparse
import logging

import uvicorn

from medibridge_api.config import get_settings

from .registry import REMOTE_AGENTS


def main() -> None:
    parser = argparse.ArgumentParser(prog="medibridge_agents.a2a")
    parser.add_argument("agent", nargs="?", choices=sorted(REMOTE_AGENTS))
    parser.add_argument("--list", action="store_true", help="print agent ids and ports")
    args = parser.parse_args()
    if args.list or not args.agent:
        for a in REMOTE_AGENTS.values():
            print(f"{a.id} {a.port}")
        return
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from .server import create_app

    agent = REMOTE_AGENTS[args.agent]
    # No access log: request lines carry nothing useful and nothing clinical should reach the log.
    uvicorn.run(create_app(agent.id), host=get_settings().a2a_host, port=agent.port, access_log=False,
                log_level="info", timeout_graceful_shutdown=5)


if __name__ == "__main__":
    main()
