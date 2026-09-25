from django.db import models

from apps.organizations.tenancy import TenantScopedModel


class Team(TenantScopedModel):
    """A group of members used for conversation routing/assignment."""

    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=80)
    description = models.CharField(max_length=255, blank=True)
    members = models.ManyToManyField(  # type: ignore[var-annotated]
        "organizations.Membership", through="TeamMembership", related_name="teams"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "slug"], name="team_org_slug_unique"),
        ]
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class TeamMembership(TenantScopedModel):
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="team_memberships")
    membership = models.ForeignKey(
        "organizations.Membership", on_delete=models.CASCADE, related_name="team_memberships"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["team", "membership"], name="team_membership_unique"),
        ]
