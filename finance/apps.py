from django.apps import AppConfig


class FinanceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "finance"

    def ready(self):
        # Importing the module registers the application's model signals.
        from . import signals  # noqa: F401
