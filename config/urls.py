"""URL configuration for the Smart Finance project."""

from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.staticfiles import finders
from django.http import FileResponse, Http404, HttpResponse
from django.urls import include, path


def health_check(request):
    return HttpResponse("ok")


def service_worker(request):
    worker_path = finders.find("pwa/service-worker.js")
    if not worker_path:
        raise Http404("Service worker not found.")

    response = FileResponse(
        open(worker_path, "rb"),
        content_type="application/javascript",
    )
    response["Cache-Control"] = "no-cache"
    response["Service-Worker-Allowed"] = "/"
    return response


urlpatterns = [
    path("health/", health_check, name="health_check"),
    path("service-worker.js", service_worker, name="service_worker"),
    path("admin/", admin.site.urls),
    path("", include("finance.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
