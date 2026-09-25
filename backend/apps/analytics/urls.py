from django.urls import path

from apps.analytics import api

urlpatterns = [
    path("overview/", api.OverviewView.as_view(), name="analytics-overview"),
    path("timeseries/", api.TimeseriesView.as_view(), name="analytics-timeseries"),
    path("numbers/", api.NumbersView.as_view(), name="analytics-numbers"),
    path("templates/", api.TemplatesView.as_view(), name="analytics-templates"),
    path("costs/", api.CostsView.as_view(), name="analytics-costs"),
    path("failures/", api.FailuresView.as_view(), name="analytics-failures"),
]
