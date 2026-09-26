from django.urls import path

from apps.organizations.api import invitations, roles, views

urlpatterns = [
    path("", views.MyOrganizationsView.as_view(), name="organization-list"),
    path("current/", views.CurrentOrganizationView.as_view(), name="organization-current"),
    path("current/members/", views.MemberListView.as_view(), name="organization-members"),
    path(
        "current/invitations/",
        invitations.InvitationListCreateView.as_view(),
        name="organization-invitations",
    ),
    path(
        "current/invitations/<uuid:pk>/",
        invitations.InvitationRevokeView.as_view(),
        name="organization-invitation-revoke",
    ),
    path("current/roles/", roles.RoleListCreateView.as_view(), name="organization-roles"),
    path(
        "current/roles/<uuid:pk>/", roles.RoleDetailView.as_view(), name="organization-role-detail"
    ),
]
