from django.urls import path
from storage_billing.api.v1.views import (
    CompanyStorageSummaryView,
    CompanyStorageHistoryView,
)

urlpatterns = [
    path('summary/', CompanyStorageSummaryView.as_view(), name='storage_summary'),
    path('history/', CompanyStorageHistoryView.as_view(), name='storage_history'),
]
