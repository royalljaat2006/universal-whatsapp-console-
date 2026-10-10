"""WebSocket endpoint: /ws/v1/realtime/?organization=<uuid>&business=<uuid>

Authentication is the same Django session as the REST API (cookie sent on
the same-origin upgrade request). The socket joins the business group only if
the user holds an active membership with ``conversations.view`` AND access to
that business, and always joins that membership's own notification group
(apps.realtime.publisher.membership_group) once authorized, so a single
socket delivers both business events and personal notifications. Clients
cannot publish; the socket is server -> client only.
"""

import uuid
from typing import Any
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from apps.businesses.models import Business
from apps.businesses.services import can_access_business
from apps.identity.authentication import session_satisfies_mfa
from apps.organizations.models import Membership
from apps.organizations.services import get_active_membership, has_permission
from apps.realtime.publisher import business_group, membership_group

CLOSE_UNAUTHORIZED = 4401
CLOSE_FORBIDDEN = 4403


@database_sync_to_async
def _mfa_satisfied(user: Any, session: Any) -> bool:
    return session_satisfies_mfa(user, session)


@database_sync_to_async
def _authorised(user: Any, organization_id: uuid.UUID, business_id: uuid.UUID) -> Membership | None:
    membership = get_active_membership(user, organization_id)
    if membership is None or not has_permission(membership, "conversations.view"):
        return None
    exists = Business.objects.filter(
        pk=business_id, organization_id=organization_id, status=Business.Status.ACTIVE
    ).exists()
    if not exists or not can_access_business(membership, business_id):
        return None
    return membership


def _uuid_param(query: dict[str, list[str]], name: str) -> uuid.UUID | None:
    try:
        return uuid.UUID((query.get(name) or [""])[0])
    except ValueError:
        return None


class RealtimeConsumer(AsyncJsonWebsocketConsumer):
    groups_joined: list[str]

    async def connect(self) -> None:
        self.groups_joined = []
        user = self.scope.get("user")
        if user is None or not user.is_authenticated:
            await self.close(code=CLOSE_UNAUTHORIZED)
            return
        # Same second-factor rule as the REST API: a session that never passed
        # MFA (e.g. created via the admin login) must not open a socket.
        if not await _mfa_satisfied(user, self.scope.get("session")):
            await self.close(code=CLOSE_UNAUTHORIZED)
            return
        query = parse_qs(self.scope.get("query_string", b"").decode())
        organization_id = _uuid_param(query, "organization")
        business_id = _uuid_param(query, "business")
        if organization_id is None or business_id is None:
            await self.close(code=CLOSE_FORBIDDEN)
            return
        membership = await _authorised(user, organization_id, business_id)
        if membership is None:
            await self.close(code=CLOSE_FORBIDDEN)
            return
        self.groups_joined = [business_group(business_id), membership_group(membership.pk)]
        for group in self.groups_joined:
            await self.channel_layer.group_add(group, self.channel_name)
        await self.accept()
        await self.send_json(
            {
                "event": "connection.ready",
                "data": {"organization": str(organization_id), "business": str(business_id)},
            }
        )

    async def disconnect(self, code: int) -> None:
        for group in self.groups_joined:
            await self.channel_layer.group_discard(group, self.channel_name)

    async def receive_json(self, content: Any, **kwargs: Any) -> None:
        # Only a heartbeat is accepted from clients.
        if isinstance(content, dict) and content.get("type") == "ping":
            await self.send_json({"event": "pong"})

    async def domain_event(self, message: dict[str, Any]) -> None:
        await self.send_json(
            {
                "event": message["event"],
                "data": message["data"],
                "emitted_at": message["emitted_at"],
            }
        )
