"""Teams API (organization-scoped).

GET  /api/v1/teams/                         list (teams.view)
POST /api/v1/teams/                         create {"name", "description"?} (teams.manage)
POST /api/v1/teams/<id>/members/            {"membership_id"} add (teams.manage)
DELETE /api/v1/teams/<id>/members/<mid>/    remove (teams.manage)
"""

from typing import Any

from django.db import IntegrityError, transaction
from django.utils.text import slugify
from rest_framework import generics, serializers, status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.services import record as audit
from apps.organizations.api.scoping import OrganizationScopedMixin
from apps.organizations.models import Membership
from apps.teams.models import Team, TeamMembership
from core.exceptions import ConflictError, NotFoundError


class TeamSerializer(serializers.ModelSerializer):
    member_ids = serializers.SerializerMethodField()

    class Meta:
        model = Team
        fields = ["id", "name", "slug", "description", "member_ids", "created_at"]
        read_only_fields = ["id", "slug", "member_ids", "created_at"]

    def get_member_ids(self, obj: Team) -> list[str]:
        return [str(tm.membership_id) for tm in obj.team_memberships.all()]


class TeamListCreateView(OrganizationScopedMixin, generics.ListAPIView):
    queryset_model = Team
    serializer_class = TeamSerializer
    pagination_class = None
    required_permissions = {"get": "teams.view", "post": "teams.manage"}

    def scope_queryset(self, queryset: Any) -> Any:
        return queryset.prefetch_related("team_memberships").order_by("name")

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        payload = TeamSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        name = payload.validated_data["name"]
        try:
            with transaction.atomic():
                team = Team.objects.create(
                    organization=self.organization,
                    name=name,
                    slug=slugify(name)[:80] or "team",
                    description=payload.validated_data.get("description", ""),
                )
        except IntegrityError as exc:
            raise ConflictError("A team with this name already exists.") from exc
        audit("team.created", organization=self.organization, actor=request.user, resource=team)
        return Response(TeamSerializer(team).data, status=status.HTTP_201_CREATED)


class TeamMembersView(OrganizationScopedMixin, APIView):
    required_permissions = {"post": "teams.manage", "delete": "teams.manage"}

    def _team(self, pk: Any) -> Team:
        team = Team.objects.for_organization(self.organization).filter(pk=pk).first()
        if team is None:
            raise NotFoundError("Team not found.")
        return team

    def post(self, request: Request, pk: str) -> Response:
        team = self._team(pk)
        membership_id = request.data.get("membership_id")
        membership = (
            Membership.objects.filter(pk=membership_id, organization=self.organization).first()
            if membership_id
            else None
        )
        if membership is None:
            raise NotFoundError("Member not found.")
        TeamMembership.objects.get_or_create(
            organization=self.organization, team=team, membership=membership
        )
        return Response(TeamSerializer(team).data)

    def delete(self, request: Request, pk: str, membership_id: str) -> Response:
        team = self._team(pk)
        TeamMembership.objects.filter(team=team, membership_id=membership_id).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
