from django.urls import path

from apps.organizations.api import views

urlpatterns = [
    path("", views.MyOrganizationsView.as_view(), name="organization-list"),
    path("current/", views.CurrentOrganizationView.as_view(), name="organization-current"),
    path("current/members/", views.MemberListView.as_view(), name="organization-members"),
]
