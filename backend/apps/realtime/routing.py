from django.urls import path

from apps.realtime.consumers import RealtimeConsumer

websocket_urlpatterns = [
    path("ws/v1/realtime/", RealtimeConsumer.as_asgi()),
]
