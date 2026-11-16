from django.db import connection
from django.http import JsonResponse


def health(request):
    """Minimal health check for uptime monitoring and deployment smoke tests.

    Confirms the application and its database connection are alive. Returns
    no version numbers, hostnames, or other sensitive details.
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        return JsonResponse({"status": "error"}, status=503)
    return JsonResponse({"status": "ok"})
