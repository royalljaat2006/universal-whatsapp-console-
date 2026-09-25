from django.urls import path

from core.health import views

urlpatterns = [
    path("live", views.live, name="health-live"),
    path("ready", views.ready, name="health-ready"),
    path("dependencies", views.dependencies, name="health-dependencies"),
]
