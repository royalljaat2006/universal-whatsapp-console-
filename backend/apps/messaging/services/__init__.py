"""Messaging domain services.

- inbound.py  — persist inbound provider messages (idempotent)
- status.py   — apply provider status reports via the state machine
- outbound.py — accept send requests (idempotent) and dispatch them
"""
