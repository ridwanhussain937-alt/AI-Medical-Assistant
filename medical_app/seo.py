import json

from django.contrib.sitemaps import Sitemap
from django.templatetags.static import static
from django.urls import reverse


INDEXABLE_ROBOTS = "index,follow,max-image-preview:large,max-snippet:-1,max-video-preview:-1"
NOINDEX_ROBOTS = "noindex,follow"

INDEXABLE_URL_NAMES = {"index", "report_intake"}

PAGE_METADATA = {
    "index": {
        "title": "AI Medical Assistant | Clinical Intake, Report Analysis and Follow-Up Support",
        "description": (
            "AI Medical Assistant helps clinicians capture symptoms, review medical images and reports, "
            "and continue patient follow-up conversations from one workspace."
        ),
    },
    "report_intake": {
        "title": "Medical Report Analysis & Comparison | AI Medical Assistant",
        "description": (
            "Upload a current report, compare it with earlier records, and generate a structured AI-assisted "
            "clinical summary inside AI Medical Assistant."
        ),
    },
    "login": {
        "title": "Secure Sign In | AI Medical Assistant",
        "description": "Secure sign-in for clinicians using AI Medical Assistant.",
    },
    "default": {
        "title": "AI Medical Assistant",
        "description": (
            "AI Medical Assistant for symptom intake, image review, report analysis, and follow-up patient conversations."
        ),
    },
}


class PublicPagesSitemap(Sitemap):
    changefreq = "weekly"

    def items(self):
        return ["index", "report_intake"]

    def location(self, item):
        return reverse(item)

    def priority(self, item):
        if item == "index":
            return 1.0
        return 0.8


def build_seo_context(request, current_language):
    resolver_match = getattr(request, "resolver_match", None)
    url_name = getattr(resolver_match, "url_name", "") or "default"
    metadata = PAGE_METADATA.get(url_name, PAGE_METADATA["default"])
    is_indexable = url_name in INDEXABLE_URL_NAMES and request.method == "GET"
    canonical_url = request.build_absolute_uri(request.path)
    og_image_url = request.build_absolute_uri(static("img/medical-assistant-logo.svg"))

    structured_data_json = ""
    if is_indexable:
        site_url = request.build_absolute_uri("/")
        page_url = canonical_url
        graph = [
            {
                "@type": "Organization",
                "name": "AI Medical Assistant",
                "url": site_url,
                "logo": og_image_url,
            },
        ]

        if url_name == "index":
            graph.append(
                {
                    "@type": "WebSite",
                    "name": "AI Medical Assistant",
                    "url": site_url,
                    "description": metadata["description"],
                    "inLanguage": current_language,
                }
            )
        else:
            graph.append(
                {
                    "@type": "WebPage",
                    "name": metadata["title"],
                    "url": page_url,
                    "description": metadata["description"],
                    "isPartOf": {"@type": "WebSite", "name": "AI Medical Assistant", "url": site_url},
                    "inLanguage": current_language,
                }
            )

        structured_data_json = json.dumps(
            {"@context": "https://schema.org", "@graph": graph},
            ensure_ascii=False,
        )

    return {
        "seo_title": metadata["title"],
        "seo_description": metadata["description"],
        "seo_robots": INDEXABLE_ROBOTS if is_indexable else NOINDEX_ROBOTS,
        "seo_canonical_url": canonical_url,
        "seo_og_type": "website",
        "seo_og_image_url": og_image_url,
        "seo_structured_data_json": structured_data_json,
        "seo_is_indexable": is_indexable,
    }
