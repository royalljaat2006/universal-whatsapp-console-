from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "core"
    label = "core"

    def ready(self) -> None:
        from core import checks  # noqa: F401  (registers the deployment checks)
