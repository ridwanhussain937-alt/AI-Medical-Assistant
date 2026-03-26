from django.urls import path
from . import views

urlpatterns = [
    path("health/", views.healthcheck_view, name="healthcheck"),
    path("set-site-language/", views.set_site_language_view, name="set_site_language"),
    path("", views.index, name="index"),
    path("reports/", views.report_intake_view, name="report_intake"),
    path("google-login/", views.google_login_start, name="google_login_start"),
    path("dashboard/", views.dashboard_view, name="dashboard"),
    path("dashboard/training-control/", views.training_control_view, name="training_control"),
    path(
        "dashboard/training-control/train-now/",
        views.training_control_train_now_view,
        name="training_control_train_now",
    ),
    path(
        "dashboard/training-control/upload/",
        views.training_control_upload_view,
        name="training_control_upload",
    ),
    path(
        "dashboard/training-control/sample-zip/",
        views.training_control_sample_zip_view,
        name="training_control_sample_zip",
    ),
    path("analyses/<int:analysis_id>/", views.analysis_detail_view, name="analysis_detail"),
    path(
        "analyses/<int:analysis_id>/treatments/<int:treatment_id>/edit/",
        views.treatment_entry_edit_view,
        name="treatment_entry_edit",
    ),
    path(
        "analyses/<int:analysis_id>/treatments/<int:treatment_id>/delete/",
        views.treatment_entry_delete_view,
        name="treatment_entry_delete",
    ),
    path("chat/", views.chat_view, name="chat"),
    path("history/", views.history_view, name="history"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("register/", views.register_view, name="register"),
    path("register/verify/<uuid:token>/", views.register_verify_view, name="register_verify"),
    path("change-credentials/", views.change_credentials_view, name="change_credentials"),
    path("dashboard/users/<int:user_id>/", views.dashboard_user_view, name="dashboard_user_view"),
    path("dashboard/users/<int:user_id>/edit/", views.dashboard_user_edit, name="dashboard_user_edit"),
    path("dashboard/users/<int:user_id>/delete/", views.dashboard_user_delete, name="dashboard_user_delete"),
]
