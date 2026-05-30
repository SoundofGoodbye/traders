"""Local, read-only web UI for the traders system.

Server-rendered FastAPI + Jinja2 over the same SQLite database the agents
use. Lives behind the optional `web` extra so the default install footprint
stays empty. The UI reads through `traders.web.queries`; write actions
(slice 13) go through the existing `traders.feedback` functions.
"""
