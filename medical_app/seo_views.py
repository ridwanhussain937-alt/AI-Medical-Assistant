from django.core.paginator import EmptyPage, PageNotAnInteger
from django.contrib.sites.requests import RequestSite
from django.http import Http404, HttpResponse
from django.template.response import TemplateResponse
from django.urls import reverse

from medical_app.seo import PublicPagesSitemap


SITEMAPS = {
    "public": PublicPagesSitemap,
}


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


def sitemap_xml_view(request):
    req_protocol = request.scheme
    req_site = RequestSite(request)
    page = request.GET.get("p", 1)

    urls = []
    for site in SITEMAPS.values():
        try:
            if callable(site):
                site = site()
            urls.extend(site.get_urls(page=page, site=req_site, protocol=req_protocol))
        except EmptyPage as exc:
            raise Http404(f"Page {page} empty") from exc
        except PageNotAnInteger as exc:
            raise Http404(f"No page '{page}'") from exc

    response = TemplateResponse(
        request,
        "sitemap.xml",
        {"urlset": urls},
        content_type="application/xml",
    )
    response.headers["X-Robots-Tag"] = "noindex, noodp, noarchive"
    return response
