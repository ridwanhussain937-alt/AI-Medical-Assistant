"""
URL configuration for ai_medical_project project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from medical_app import views as medical_views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("healthz", medical_views.healthcheck_view, name="healthcheck"),
    path("accounts/login/", medical_views.allauth_login_redirect),
    path("accounts/signup/", medical_views.allauth_signup_redirect),
    path("accounts/", include("allauth.urls")),
    path("", include("medical_app.urls")),
]

if settings.DEBUG or settings.SERVE_MEDIA_FILES:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
