"""Shared kernel: cross-cutting infrastructure used by every domain module.

`core` must never import from `apps.*` (dependency direction: apps -> core).
"""
