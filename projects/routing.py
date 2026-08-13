# --------------------------------------------------------------------------------
#       Projects - Real-Time Chat WebSocket Routing
# --------------------------------------------------------------------------------

from django.urls import re_path
from projects.consumers import ChatConsumer

websocket_urlpatterns = [
    re_path(r'^ws/chat/(?P<room_type>project|story|task|epic)/(?P<room_id>\d+)/?$', ChatConsumer.as_asgi()),
    re_path(r'^ws/comments/(?P<room_type>project|story|task|epic)/(?P<room_id>\d+)/?$', ChatConsumer.as_asgi()),
]
