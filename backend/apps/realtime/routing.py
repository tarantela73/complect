from django.urls import re_path

from apps.realtime.consumers import ErpRealtimeConsumer

# Контракт путей подключения к Realtime Mesh платформы Комплект
websocket_urlpatterns = [
    # wss://erp.complect.ru/ws/realtime/
    re_path(r"^ws/realtime/$", ErpRealtimeConsumer.as_asgi()),
]
