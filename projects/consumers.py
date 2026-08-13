# --------------------------------------------------------------------------------
#       Projects - Real-Time Chat WebSocket Consumer
# --------------------------------------------------------------------------------

import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from projects.selectors.projects import projects_for_user
from projects.selectors.stories import stories_for_user
from projects.selectors.tasks import tasks_for_user
from projects.selectors.epics import epics_for_user


class ChatConsumer(AsyncWebsocketConsumer):
    """
    WebSocket Consumer for real-time room-based Chat & Comments synchronization.
    Supports room types: 'project', 'story', 'task', 'epic'.
    """

    @database_sync_to_async
    def check_room_permission(self, user, room_type, room_id):
        if not user or not user.is_authenticated:
            return False, None

        try:
            room_id_int = int(room_id)
            if room_type == 'project':
                proj = projects_for_user(user).filter(id=room_id_int).first()
                if proj:
                    return True, proj.id
            elif room_type == 'story':
                story = stories_for_user(user).filter(id=room_id_int).select_related('project').first()
                if story:
                    return True, story.project_id
            elif room_type == 'task':
                task = tasks_for_user(user).filter(id=room_id_int).select_related('story__project').first()
                if task and task.story:
                    return True, task.story.project_id
            elif room_type == 'epic':
                epic = epics_for_user(user).filter(id=room_id_int).first()
                if epic:
                    return True, epic.project_id
        except Exception:
            pass

        return False, None

    async def connect(self):
        self.room_type = self.scope['url_route']['kwargs'].get('room_type', 'project')
        self.room_id = self.scope['url_route']['kwargs'].get('room_id')
        self.user = self.scope.get('user')

        # Verify authentication and permission
        has_perm, project_id = await self.check_room_permission(self.user, self.room_type, self.room_id)
        if not has_perm:
            # Reject connection with 4003 Forbidden
            await self.close(code=4003)
            return

        self.room_group_name = f"chat_{self.room_type}_{self.room_id}"

        # Join room group
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )

        await self.accept()

        # Send connection confirmation
        await self.send(text_data=json.dumps({
            'type': 'connection_established',
            'status': 'connected',
            'room_type': self.room_type,
            'room_id': self.room_id,
        }))

    async def disconnect(self, close_code):
        if hasattr(self, 'room_group_name'):
            await self.channel_layer.group_discard(
                self.room_group_name,
                self.channel_name
            )

    async def receive(self, text_data):
        if not text_data:
            return
        try:
            data = json.loads(text_data)
            action_type = data.get('type')
            if action_type in ['typing_start', 'typing_stop']:
                user_id = getattr(self.user, 'id', None)
                first_name = getattr(self.user, 'first_name', '')
                last_name = getattr(self.user, 'last_name', '')
                email = getattr(self.user, 'email', 'Team Member')
                user_name = f"{first_name} {last_name}".strip() or email
                if user_id and hasattr(self, 'room_group_name'):
                    await self.channel_layer.group_send(
                        self.room_group_name,
                        {
                            'type': 'typing_indicator',
                            'event_type': 'typing_indicator',
                            'data': {
                                'user_id': user_id,
                                'user_name': user_name,
                                'is_typing': (action_type == 'typing_start'),
                            }
                        }
                    )
        except Exception:
            pass

    async def chat_message(self, event):
        """
        Handler for broadcasting chat messages/comments to connected WebSocket clients.
        """
        await self.send(text_data=json.dumps({
            'event_type': event.get('event_type', 'message_created'),
            'data': event.get('data', {}),
        }))

    async def typing_indicator(self, event):
        """
        Handler for forwarding typing indicator events to WebSocket clients.
        """
        await self.send(text_data=json.dumps({
            'event_type': 'typing_indicator',
            'data': event.get('data', {}),
        }))
