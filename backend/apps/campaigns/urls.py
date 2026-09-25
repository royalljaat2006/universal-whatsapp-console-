from django.urls import path

from apps.campaigns import api

urlpatterns = [
    path("", api.CampaignListCreateView.as_view(), name="campaign-list"),
    path("audience-preview/", api.AudiencePreviewView.as_view(), name="campaign-audience-preview"),
    path("<uuid:pk>/", api.CampaignDetailView.as_view(), name="campaign-detail"),
    path("<uuid:pk>/preview/", api.CampaignPreviewView.as_view(), name="campaign-preview"),
    path("<uuid:pk>/launch/", api.CampaignLaunchView.as_view(), name="campaign-launch"),
    path("<uuid:pk>/pause/", api.CampaignPauseView.as_view(), name="campaign-pause"),
    path("<uuid:pk>/resume/", api.CampaignResumeView.as_view(), name="campaign-resume"),
    path("<uuid:pk>/cancel/", api.CampaignCancelView.as_view(), name="campaign-cancel"),
    path("<uuid:pk>/retry-failed/", api.CampaignRetryView.as_view(), name="campaign-retry"),
    path("<uuid:pk>/report/", api.CampaignReportView.as_view(), name="campaign-report"),
    path("<uuid:pk>/failures/", api.CampaignFailuresView.as_view(), name="campaign-failures"),
    path("<uuid:pk>/recipients/", api.CampaignRecipientsView.as_view(), name="campaign-recipients"),
    path("<uuid:pk>/events/", api.CampaignEventsView.as_view(), name="campaign-events"),
]
