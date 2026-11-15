from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    # The schedule is the application's landing page.
    path("", RedirectView.as_view(pattern_name="schedule"), name="home"),
    path("accounts/", include("accounts.urls")),
    path("laundry/", include("laundry.urls")),
    path("admin/", admin.site.urls),
]
