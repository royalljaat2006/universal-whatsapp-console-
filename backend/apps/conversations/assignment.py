"""Conversation assignment (public interface).

A conversation may be unassigned, assigned to a member, to a team, or both
(team queue + owner). The assignee must be an ACTIVE member of the same
organization with access to the conversation's business; a team must belong
to the organization. Every change appends a ConversationAssignment row and
emits ``conversation.assigned``.
"""

from typing import Any

from django.db import transaction

from apps.audit import actions
from apps.audit.services import record as audit
from apps.businesses.services import can_access_business
from apps.conversations.models import Conversation, ConversationAssignment
from apps.messaging.services import events
from apps.notifications import services as notifications
from apps.organizations.models import Membership
from apps.teams.models import Team
from core.exceptions import DomainError


class InvalidAssigneeError(DomainError):
    code = "INVALID_ASSIGNEE"
    status_code = 422


def _membership(conversation: Conversation, membership_id: Any) -> Membership:
    membership = (
        Membership.objects.select_related("user")
        .filter(
            pk=membership_id,
            organization_id=conversation.organization_id,
            status=Membership.Status.ACTIVE,
        )
        .first()
    )
    if membership is None or not can_access_business(membership, conversation.business_id):
        raise InvalidAssigneeError("The assignee has no access to this conversation's business.")
    return membership


def _team(conversation: Conversation, team_id: Any) -> Team:
    team = Team.objects.filter(pk=team_id, organization_id=conversation.organization_id).first()
    if team is None:
        raise InvalidAssigneeError("Team not found.")
    return team


@transaction.atomic
def assign(
    conversation: Conversation,
    *,
    membership_id: Any = None,
    team_id: Any = None,
    actor: Any = None,
    request: Any = None,
) -> Conversation:
    conversation = Conversation.objects.select_for_update().get(pk=conversation.pk)
    new_membership = _membership(conversation, membership_id) if membership_id else None
    new_team = _team(conversation, team_id) if team_id else None
    if conversation.assigned_membership_id == (
        new_membership.pk if new_membership else None
    ) and conversation.assigned_team_id == (new_team.pk if new_team else None):
        return conversation
    ConversationAssignment.objects.create(
        organization_id=conversation.organization_id,
        business_id=conversation.business_id,
        conversation=conversation,
        previous_membership_id=conversation.assigned_membership_id,
        previous_team_id=conversation.assigned_team_id,
        new_membership=new_membership,
        new_team=new_team,
        assigned_by=actor if getattr(actor, "is_authenticated", False) else None,
    )
    conversation.assigned_membership = new_membership
    conversation.assigned_team = new_team
    conversation.save(update_fields=["assigned_membership", "assigned_team", "updated_at"])
    audit(
        actions.CONVERSATION_ASSIGNED,
        organization=conversation.organization,
        actor=actor,
        resource=conversation,
        metadata={
            "membership_id": str(new_membership.pk) if new_membership else None,
            "team_id": str(new_team.pk) if new_team else None,
        },
        request=request,
    )
    events.conversation_assigned(conversation)
    if new_membership is not None and new_membership.user_id != getattr(actor, "pk", None):
        notifications.notify(
            organization=conversation.organization,
            recipient=new_membership,
            kind=notifications.KIND_CONVERSATION_ASSIGNED,
            title="A conversation was assigned to you",
            body=conversation.last_message_preview or "",
            data={"conversation_id": str(conversation.pk), "business_id": str(conversation.business_id)},
        )
    return conversation
