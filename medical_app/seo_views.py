from django.http import HttpResponse
from django.urls import reverse


def robots_txt_view(request):
    sitemap_url = request.build_absolute_uri(reverse("sitemap"))
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /admin/",
        "Disallow: /dashboard/",
        "Disallow: /chat/",
        "Disallow: /history/",
        "Disallow: /analyses/",
        "Disallow: /change-credentials/",
        "Disallow: /accounts/",
        "Sitemap: " + sitemap_url,
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain; charset=utf-8")
