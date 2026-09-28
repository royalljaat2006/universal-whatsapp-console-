"""URL map.

/api/v1/...        versioned REST API (JSON, session auth, tenant-scoped)
/webhooks/v1/...   provider callbacks (signature-verified, no session)
/health/...        liveness / readiness
/metrics           Prometheus scrape target, token-gated (core.metrics)
/ws/v1/...         WebSockets (routed in config/asgi.py)
"""

from django.conf import settings
from django.contrib import admin
from django.urls import URLPattern, URLResolver, include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from apps.audit.api import AuditLogListView
from apps.businesses.access_api import BusinessAccessRevokeView, BusinessAccessView, MemberScopeView
from apps.contacts import api as contacts_api
from apps.conversations import api as conversations_api
from apps.messaging.api import MessageStatusHistoryView
from apps.notifications import api as notifications_api
from apps.organizations.api import invitations as invitations_api
from apps.teams import api as teams_api
from apps.templates import api as templates_api
from apps.webhooks.views import meta_whatsapp_webhook
from core.health import views as health_views

api_v1: list[URLPattern | URLResolver] = [
    path("auth/", include("apps.identity.urls")),
    path("organizations/", include("apps.organizations.urls")),
    path("api-keys/", include("apps.apikeys.urls")),
    path("platform/", include("apps.platform.urls")),
    path("businesses/", include("apps.businesses.urls")),
    path("whatsapp/", include("apps.whatsapp.urls")),
    path("businesses/<uuid:pk>/access/", BusinessAccessView.as_view(), name="business-access"),
    path(
        "businesses/<uuid:pk>/access/<uuid:membership_id>/",
        BusinessAccessRevokeView.as_view(),
        name="business-access-revoke",
    ),
    path(
        "organizations/current/members/<uuid:membership_id>/",
        MemberScopeView.as_view(),
        name="member-scope",
    ),
    path("contacts/", contacts_api.ContactListView.as_view(), name="contact-list"),
    path(
        "contacts/import/",
        contacts_api.ContactImportListCreateView.as_view(),
        name="contact-import-list",
    ),
    path(
        "contacts/import/<uuid:pk>/",
        contacts_api.ContactImportDetailView.as_view(),
        name="contact-import-detail",
    ),
    path("contacts/<uuid:pk>/", contacts_api.ContactDetailView.as_view(), name="contact-detail"),
    path(
        "contacts/<uuid:pk>/notes/", contacts_api.ContactNotesView.as_view(), name="contact-notes"
    ),
    path(
        "contacts/<uuid:pk>/notes/<uuid:note_id>/",
        contacts_api.ContactNoteDetailView.as_view(),
        name="contact-note-detail",
    ),
    path(
        "contacts/<uuid:pk>/erase/", contacts_api.ContactEraseView.as_view(), name="contact-erase"
    ),
    path("contacts/<uuid:pk>/tags/", contacts_api.ContactTagView.as_view(), name="contact-tags"),
    path(
        "contacts/<uuid:pk>/tags/<uuid:tag_id>/",
        contacts_api.ContactTagView.as_view(),
        name="contact-tag-remove",
    ),
    path(
        "contacts/<uuid:pk>/history/",
        contacts_api.ContactHistoryView.as_view(),
        name="contact-history",
    ),
    path("tags/", contacts_api.TagListCreateView.as_view(), name="tag-list"),
    path("tags/<uuid:pk>/", contacts_api.TagDetailView.as_view(), name="tag-detail"),
    path("teams/", teams_api.TeamListCreateView.as_view(), name="team-list"),
    path("teams/<uuid:pk>/members/", teams_api.TeamMembersView.as_view(), name="team-members"),
    path(
        "teams/<uuid:pk>/members/<uuid:membership_id>/",
        teams_api.TeamMembersView.as_view(),
        name="team-member-remove",
    ),
    path(
        "conversations/", conversations_api.ConversationListView.as_view(), name="conversation-list"
    ),
    path(
        "conversations/assignees/",
        conversations_api.AssigneeOptionsView.as_view(),
        name="conversation-assignees",
    ),
    path(
        "conversations/<uuid:pk>/",
        conversations_api.ConversationDetailView.as_view(),
        name="conversation-detail",
    ),
    path(
        "conversations/<uuid:pk>/read/",
        conversations_api.ConversationReadView.as_view(),
        name="conversation-read",
    ),
    path(
        "conversations/<uuid:pk>/assign/",
        conversations_api.ConversationAssignView.as_view(),
        name="conversation-assign",
    ),
    path(
        "conversations/<uuid:pk>/assignments/",
        conversations_api.ConversationAssignmentHistoryView.as_view(),
        name="conversation-assignments",
    ),
    path(
        "conversations/<uuid:pk>/tags/",
        conversations_api.ConversationTagView.as_view(),
        name="conversation-tags",
    ),
    path(
        "conversations/<uuid:pk>/tags/<uuid:tag_id>/",
        conversations_api.ConversationTagView.as_view(),
        name="conversation-tag-remove",
    ),
    path("messages/", include("apps.messaging.urls")),
    path(
        "messages/<uuid:pk>/status-history/",
        MessageStatusHistoryView.as_view(),
        name="message-status-history",
    ),
    path("media/", include("apps.media.urls")),
    path("templates/", templates_api.MessageTemplateListView.as_view(), name="template-list"),
    path("templates/sync/", templates_api.TemplateSyncView.as_view(), name="template-sync"),
    path(
        "templates/<uuid:pk>/",
        templates_api.MessageTemplateDetailView.as_view(),
        name="template-detail",
    ),
    path("audit-logs/", AuditLogListView.as_view(), name="audit-log-list"),
    path("notifications/", notifications_api.NotificationListView.as_view(), name="notification-list"),
    path(
        "notifications/unread-count/",
        notifications_api.NotificationUnreadCountView.as_view(),
        name="notification-unread-count",
    ),
    path(
        "notifications/read-all/",
        notifications_api.NotificationMarkAllReadView.as_view(),
        name="notification-read-all",
    ),
    path(
        "notifications/preferences/",
        notifications_api.NotificationPreferenceView.as_view(),
        name="notification-preferences",
    ),
    path(
        "notifications/<uuid:pk>/read/",
        notifications_api.NotificationMarkReadView.as_view(),
        name="notification-mark-read",
    ),
    path(
        "invitations/<uuid:pk>/",
        invitations_api.InvitationPreviewView.as_view(),
        name="invitation-preview",
    ),
    path(
        "invitations/<uuid:pk>/accept/",
        invitations_api.InvitationAcceptView.as_view(),
        name="invitation-accept",
    ),
    path("analytics/", include("apps.analytics.urls")),
    path("campaigns/", include("apps.campaigns.urls")),
    path("audiences/", include("apps.campaigns.audience_urls")),
    path("exports/", include("apps.reports.urls")),
    path("schema/", SpectacularAPIView.as_view(), name="openapi-schema"),
]

if settings.DEBUG:
    api_v1.append(path("docs/", SpectacularSwaggerView.as_view(url_name="openapi-schema")))

urlpatterns = [
    path("api/v1/", include(api_v1)),
    # Both spellings: Meta posts to exactly the configured callback URL and a
    # slash redirect would turn its POST into a failed delivery.
    path("webhooks/v1/meta/whatsapp/", meta_whatsapp_webhook, name="webhook-meta-whatsapp"),
    path("webhooks/v1/meta/whatsapp", meta_whatsapp_webhook),
    path("health/", include("core.health.urls")),
    path("metrics", health_views.metrics, name="metrics"),
    path("admin/", admin.site.urls),
]
