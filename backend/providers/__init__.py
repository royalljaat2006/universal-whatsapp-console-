"""Provider integration layer.

Everything provider-specific (HTTP, payload shapes, error codes, webhook
formats) lives under ``providers/``. Domain modules depend only on the
interfaces and value types in ``providers.whatsapp.base`` / ``.types``.
Providers never import domain models (``apps.*``): they take and return
plain value objects.
"""
