import csv
import json
import shutil
import uuid
import zipfile
from io import BytesIO, StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .analysis_engine import analyze_report_text
from .dataset_importer import load_classifier_records, load_qa_corpus_entries
from .model_evaluation import load_evaluation_report
from .models import (
    AIModelConfiguration,
    AITrainingRun,
    ChatMessage,
    ClinicalKnowledgeEntry,
    FeaturedImage,
    LoginActivity,
    MedicalAnalysis,
    PendingRegistration,
    TrainingDatasetUpload,
    TreatmentEntry,
    TreatmentTrainingRecord,
    UserProfile,
)
from .services.site_language import SITE_LANGUAGE_SESSION_KEY

user_model = get_user_model()


class FakePredictionModel:
    def __init__(self, prediction):
        self.prediction = prediction

    def predict(self, texts):
        return [self.prediction for _ in texts]


def write_csv_dataset(csv_path, fieldnames, rows):
    with csv_path.open("w", encoding="utf-8", newline="") as dataset_file:
        writer = csv.DictWriter(dataset_file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_zipped_csv(zip_path, member_name, fieldnames, rows):
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        with archive.open(member_name, "w") as archive_file:
            text_stream = StringIO()
            writer = csv.DictWriter(text_stream, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
            archive_file.write(text_stream.getvalue().encode("utf-8"))


def make_scratch_dir(prefix):
    scratch_root = Path.cwd() / "_test_scratch"
    scratch_root.mkdir(parents=True, exist_ok=True)
    scratch_dir = scratch_root / f"{prefix}-{uuid.uuid4().hex}"
    scratch_dir.mkdir(parents=True, exist_ok=False)
    return scratch_dir


def cleanup_scratch_dir(path):
    shutil.rmtree(path, ignore_errors=True)


class PublicPageTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_homepage_loads_featured_images(self):
        response = self.client.get(reverse("index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Quick Access")
        self.assertContains(response, "Important Notice")
        self.assertTrue(FeaturedImage.objects.exists())
        self.assertContains(response, "medical-assistant-logo.svg")

    def test_homepage_moves_report_tools_into_collapsible_panel(self):
        response = self.client.get(reverse("index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Open Reports &amp; Comparison")
        self.assertContains(response, reverse("report_intake"))
        self.assertNotContains(response, "report-tools-panel")
        self.assertContains(response, "upload.js")

    def test_report_workspace_page_loads_report_fields(self):
        response = self.client.get(reverse("report_intake"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Current Medical Report")
        self.assertContains(response, "Previous Report Comparison")
        self.assertContains(response, "Analyze Reports")

    def test_healthcheck_endpoint_returns_ok(self):
        response = self.client.get(reverse("healthcheck"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")


class DeploymentCommandTests(TestCase):
    def test_configure_site_updates_current_site(self):
        call_command(
            "configure_site",
            "--domain=demo.example.com",
            "--name=AI Medical Assistant Demo",
        )

        site = Site.objects.get(pk=1)
        self.assertEqual(site.domain, "demo.example.com")
        self.assertEqual(site.name, "AI Medical Assistant Demo")


class SiteLanguageTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = user_model.objects.create_user(
            username="language_user",
            email="language_user@example.com",
            password="SecurePass123!",
        )
        UserProfile.objects.update_or_create(
            user=self.user,
            defaults={"mobile_number": "9999991111"},
        )

    def test_anonymous_language_switch_updates_session(self):
        response = self.client.get(
            reverse("set_site_language"),
            {
                "language": "hindi",
                "next": reverse("index"),
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("index"))
        self.assertEqual(self.client.session[SITE_LANGUAGE_SESSION_KEY], "hindi")

    def test_authenticated_language_switch_updates_profile_and_session(self):
        self.client.login(username="language_user", password="SecurePass123!")

        response = self.client.get(
            reverse("set_site_language"),
            {
                "language": "arabic",
                "next": reverse("dashboard"),
            },
        )

        self.user.refresh_from_db()
        self.user.profile.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("dashboard"))
        self.assertEqual(self.client.session[SITE_LANGUAGE_SESSION_KEY], "arabic")
        self.assertEqual(self.user.profile.language_preference, "arabic")


class LoginPageTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_allauth_login_redirects_to_branded_login(self):
        response = self.client.get("/accounts/login/?next=/dashboard/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/login/?next=%2Fdashboard%2F")

    def test_allauth_signup_redirects_to_google_signup_flow(self):
        response = self.client.get("/accounts/signup/?next=/dashboard/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/google-login/?next=%2Fdashboard%2F")

    @override_settings(
        GOOGLE_OAUTH_CLIENT_ID="",
        GOOGLE_OAUTH_CLIENT_SECRET="",
    )
    def test_login_page_hides_google_button_when_oauth_not_configured(self):
        response = self.client.get(reverse("login"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'class="google-signin-button"', html=False)
        self.assertNotContains(response, "or continue with password")

    @override_settings(
        GOOGLE_OAUTH_CLIENT_ID="",
        GOOGLE_OAUTH_CLIENT_SECRET="",
    )
    def test_google_login_route_returns_to_login_when_oauth_not_configured(self):
        response = self.client.get(f"{reverse('google_login_start')}?next=/dashboard/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/login/?next=%2Fdashboard%2F")

    @override_settings(
        GOOGLE_OAUTH_CLIENT_ID="google-client-id",
        GOOGLE_OAUTH_CLIENT_SECRET="google-client-secret",
    )
    def test_login_page_shows_active_google_button_when_oauth_configured(self):
        response = self.client.get(reverse("login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Continue with Google")
        self.assertContains(response, reverse("google_login_start"))
        self.assertContains(response, "Google opens its account chooser directly")

    @override_settings(
        GOOGLE_OAUTH_CLIENT_ID="google-client-id",
        GOOGLE_OAUTH_CLIENT_SECRET="google-client-secret",
    )
    def test_google_login_route_redirects_to_provider_when_oauth_configured(self):
        response = self.client.get(f"{reverse('google_login_start')}?next=/dashboard/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.headers["Location"],
            "/accounts/google/login/?next=%2Fdashboard%2F",
        )

    @override_settings(
        GOOGLE_OAUTH_CLIENT_ID="google-client-id",
        GOOGLE_OAUTH_CLIENT_SECRET="google-client-secret",
    )
    def test_register_route_redirects_to_google_signup_flow(self):
        response = self.client.get(f"{reverse('register')}?next=/dashboard/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/google-login/?next=%2Fdashboard%2F")

    @override_settings(
        GOOGLE_OAUTH_CLIENT_ID="google-client-id",
        GOOGLE_OAUTH_CLIENT_SECRET="google-client-secret",
    )
    def test_register_verify_route_redirects_to_google_signup_flow(self):
        pending = PendingRegistration.objects.create(
            first_name="Ava",
            last_name="Stone",
            email="ava@example.com",
            mobile_number="",
            password_hash="placeholder",
        )

        response = self.client.get(reverse("register_verify", args=[pending.verification_token]))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/google-login/?next=%2Fdashboard%2F")


class SeoSurfaceTests(TestCase):
    def test_homepage_contains_canonical_robots_and_structured_data(self):
        response = self.client.get(reverse("index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            '<meta name="robots" content="index,follow,max-image-preview:large,max-snippet:-1,max-video-preview:-1">',
            html=False,
        )
        self.assertContains(response, '<link rel="canonical" href="http://testserver/">', html=False)
        self.assertContains(response, '"@context": "https://schema.org"', html=False)
        self.assertContains(response, "Clinical Intake, Report Analysis and Follow-Up Support")

    def test_login_page_is_noindex(self):
        response = self.client.get(reverse("login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            '<meta name="robots" content="noindex,follow">',
            html=False,
        )

    def test_robots_txt_includes_sitemap_and_private_disallows(self):
        response = self.client.get(reverse("robots_txt"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "User-agent: *")
        self.assertContains(response, "Disallow: /admin/")
        self.assertContains(response, "Disallow: /dashboard/")
        self.assertContains(response, "Sitemap: http://testserver/sitemap.xml")

    def test_sitemap_lists_public_pages(self):
        response = self.client.get(reverse("sitemap"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "http://testserver/")
        self.assertContains(response, "http://testserver/reports/")
        self.assertNotContains(response, "http://testserver/chat/")


class AnalysisEngineTests(TestCase):
    @patch("medical_app.analysis_engine._load_pickle_model")
    def test_report_analysis_prefers_heuristics_when_trained_model_disagrees(self, mock_model_loader):
        mock_model_loader.return_value = FakePredictionModel("Bronchitis")

        result = analyze_report_text(
            "Persistent cough with mild fever for three days. Inflammation markers are elevated."
        )

        self.assertEqual(result["predicted_condition"], "Infection")
        self.assertEqual(result["model_source"], "heuristic")

    @patch("medical_app.analysis_engine._load_pickle_model")
    def test_report_analysis_uses_trained_model_when_prediction_matches_supported_label(
        self,
        mock_model_loader,
    ):
        mock_model_loader.return_value = FakePredictionModel("Respiratory")

        result = analyze_report_text(
            "Patient has persistent cough and wheeze with bronchial irritation."
        )

        self.assertEqual(result["predicted_condition"], "Respiratory")
        self.assertEqual(result["model_source"], "trained-model")


class DatasetImportTests(TestCase):
    def test_load_classifier_records_reads_zip_files_and_excludes_noisy_sources_by_default(self):
        dataset_dir = make_scratch_dir("dataset-import-classifier")
        try:
            write_csv_dataset(
                dataset_dir / "medical_data.csv",
                ["Patient_Problem", "Disease", "Prescription"],
                [
                    {
                        "Patient_Problem": "Recurring wheeze and chest tightness",
                        "Disease": "Asthma",
                        "Prescription": "Inhaler support",
                    }
                ],
            )
            write_zipped_csv(
                dataset_dir / "Diseases_Symptoms.csv.zip",
                "Diseases_Symptoms.csv",
                ["Name", "Symptoms", "Treatments", "Disease_Code", "Contagious", "Chronic"],
                [
                    {
                        "Name": "Eczema",
                        "Symptoms": "itchy skin and rash",
                        "Treatments": "moisturizers",
                        "Disease_Code": "D1",
                        "Contagious": "False",
                        "Chronic": "True",
                    }
                ],
            )
            write_zipped_csv(
                dataset_dir / "medical_question_answer_dataset_50000.csv.zip",
                "medical_question_answer_dataset_50000.csv",
                ["ID", "Symptoms/Question", "Disease Prediction", "Recommended Medicines", "Advice"],
                [
                    {
                        "ID": "1",
                        "Symptoms/Question": "persistent cough with mucus",
                        "Disease Prediction": "Bronchitis",
                        "Recommended Medicines": "Azithromycin",
                        "Advice": "Drink fluids",
                    },
                    {
                        "ID": "2",
                        "Symptoms/Question": "persistent cough with mucus",
                        "Disease Prediction": "Bronchitis",
                        "Recommended Medicines": "Azithromycin",
                        "Advice": "Drink fluids",
                    },
                ],
            )
            write_zipped_csv(
                dataset_dir / "train.csv.zip",
                "train.csv",
                ["qtype", "Question", "Answer"],
                [
                    {
                        "qtype": "symptoms",
                        "Question": "What are the symptoms of malaria?",
                        "Answer": "Fever and chills.",
                    }
                ],
            )

            records, summary = load_classifier_records(
                dataset_dir,
                dedupe=True,
                minimum_occurrences=1,
            )

            self.assertEqual(len(records), 3)
            self.assertEqual(summary["duplicates_removed"], 1)
            self.assertFalse(any(record["source"] == "train.csv" for record in records))
            self.assertTrue(summary["datasets"]["medical_data.csv"]["found"])
            self.assertTrue(summary["datasets"]["Diseases_Symptoms.csv"]["found"])
            self.assertTrue(summary["datasets"]["medical_question_answer_dataset_50000.csv"]["found"])
        finally:
            cleanup_scratch_dir(dataset_dir)

    def test_load_qa_corpus_entries_deduplicates_exact_duplicate_pairs(self):
        dataset_dir = make_scratch_dir("dataset-import-qa")
        try:
            write_csv_dataset(
                dataset_dir / "medical_data.csv",
                ["Patient_Problem", "Disease", "Prescription"],
                [
                    {
                        "Patient_Problem": "Constant fatigue and muscle weakness",
                        "Disease": "Chronic Fatigue Syndrome",
                        "Prescription": "graded exercise",
                    }
                ],
            )
            write_csv_dataset(
                dataset_dir / "Diseases_Symptoms.csv",
                ["Name", "Symptoms", "Treatments", "Disease_Code", "Contagious", "Chronic"],
                [
                    {
                        "Name": "Migraine",
                        "Symptoms": "head pain with light sensitivity",
                        "Treatments": "rest in a dark room",
                        "Disease_Code": "D2",
                        "Contagious": "False",
                        "Chronic": "True",
                    }
                ],
            )
            write_csv_dataset(
                dataset_dir / "medical_question_answer_dataset_50000.csv",
                ["ID", "Symptoms/Question", "Disease Prediction", "Recommended Medicines", "Advice"],
                [
                    {
                        "ID": "1",
                        "Symptoms/Question": "muscle cramps and weakness",
                        "Disease Prediction": "Electrolyte Imbalance",
                        "Recommended Medicines": "Electrolyte solution",
                        "Advice": "Stay hydrated",
                    },
                    {
                        "ID": "2",
                        "Symptoms/Question": "muscle cramps and weakness",
                        "Disease Prediction": "Electrolyte Imbalance",
                        "Recommended Medicines": "Electrolyte solution",
                        "Advice": "Stay hydrated",
                    },
                ],
            )

            entries, summary = load_qa_corpus_entries(dataset_dir, dedupe=True)

            self.assertEqual(len(entries), 3)
            self.assertEqual(summary["duplicates_removed"], 1)
            self.assertEqual(summary["source_distribution"]["medical_question_answer_dataset_50000.csv"], 1)
        finally:
            cleanup_scratch_dir(dataset_dir)

    def test_import_external_datasets_dry_run_reports_clean_records(self):
        dataset_dir = make_scratch_dir("dataset-import-command")
        try:
            write_csv_dataset(
                dataset_dir / "medical_data.csv",
                ["Patient_Problem", "Disease", "Prescription"],
                [
                    {
                        "Patient_Problem": "Recurring wheeze and chest tightness",
                        "Disease": "Asthma",
                        "Prescription": "Inhaler support",
                    }
                ],
            )
            write_csv_dataset(
                dataset_dir / "Diseases_Symptoms.csv",
                ["Name", "Symptoms", "Treatments", "Disease_Code", "Contagious", "Chronic"],
                [
                    {
                        "Name": "Eczema",
                        "Symptoms": "itchy skin and rash",
                        "Treatments": "moisturizers",
                        "Disease_Code": "D1",
                        "Contagious": "False",
                        "Chronic": "True",
                    }
                ],
            )
            write_csv_dataset(
                dataset_dir / "medical_question_answer_dataset_50000.csv",
                ["ID", "Symptoms/Question", "Disease Prediction", "Recommended Medicines", "Advice"],
                [
                    {
                        "ID": "1",
                        "Symptoms/Question": "persistent cough with mucus",
                        "Disease Prediction": "Bronchitis",
                        "Recommended Medicines": "Azithromycin",
                        "Advice": "Drink fluids",
                    },
                    {
                        "ID": "2",
                        "Symptoms/Question": "persistent cough with mucus",
                        "Disease Prediction": "Bronchitis",
                        "Recommended Medicines": "Azithromycin",
                        "Advice": "Drink fluids",
                    },
                ],
            )

            output = StringIO()
            call_command(
                "import_external_datasets",
                datasets_dir=str(dataset_dir),
                dry_run=True,
                dedupe=True,
                minimum_condition_occurrences=1,
                stdout=output,
            )

            self.assertIn("Would create 3 external training records", output.getvalue())
        finally:
            cleanup_scratch_dir(dataset_dir)


class RegistrationTests(TestCase):
    @override_settings(
        GOOGLE_OAUTH_CLIENT_ID="google-client-id",
        GOOGLE_OAUTH_CLIENT_SECRET="google-client-secret",
    )
    def test_register_post_redirects_to_google_login_without_creating_pending_registration(self):
        response = self.client.post(
            reverse("register"),
            {
                "first_name": "Ava",
                "last_name": "Stone",
                "email": "ava@example.com",
                "password1": "StrongPass123!",
                "password2": "StrongPass123!",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/google-login/?next=%2Fdashboard%2F")
        self.assertFalse(PendingRegistration.objects.filter(email="ava@example.com").exists())
        self.assertFalse(user_model.objects.filter(email="ava@example.com").exists())

    @override_settings(
        GOOGLE_OAUTH_CLIENT_ID="google-client-id",
        GOOGLE_OAUTH_CLIENT_SECRET="google-client-secret",
    )
    def test_verify_route_redirects_legacy_tokens_to_google_login(self):
        pending = PendingRegistration.objects.create(
            first_name="Ava",
            last_name="Stone",
            email="ava@example.com",
            mobile_number="",
            password_hash="placeholder",
        )

        response = self.client.post(
            reverse("register_verify", args=[pending.verification_token]),
            {"email_otp": "123456"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/google-login/?next=%2Fdashboard%2F")
        self.assertTrue(PendingRegistration.objects.filter(email="ava@example.com").exists())


class ChatTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = user_model.objects.create_user(
            username="clinician",
            email="clinician@example.com",
            password="SecurePass123!",
        )
        UserProfile.objects.update_or_create(
            user=self.user,
            defaults={
                "mobile_number": "9999999999",
                "language_preference": "hindi",
                "response_style": "clinical",
                "ai_risk_preference": "conservative",
            },
        )

    def test_chat_requires_login(self):
        response = self.client.get(reverse("chat"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    @patch(
        "medical_app.views.answer_question",
        return_value={
            "answer": "Possible condition: Bronchitis. Recommended medicines: Azithromycin.",
            "score": 0.71,
            "source_metadata": {
                "source": "medical_question_answer_dataset_50000.csv",
                "condition": "Bronchitis",
            },
            "used_local_qa": True,
        },
    )
    @patch("medical_app.views.analyze_image_with_query")
    def test_chat_uses_local_qa_answer_for_high_confidence_text_queries(self, mock_ai, mock_local_qa):
        self.client.login(username="clinician", password="SecurePass123!")

        response = self.client.post(
            reverse("chat"),
            {"message": "persistent cough with mucus"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Possible condition: Bronchitis")
        self.assertContains(response, "Source: medical_question_answer_dataset_50000.csv (Bronchitis)")
        mock_ai.assert_not_called()

    @patch("medical_app.views.answer_question", return_value={"used_local_qa": False})
    @patch("medical_app.views.analyze_image_with_query", return_value="Structured reply.")
    def test_chat_escapes_rendered_message_content(self, mock_ai, mock_local_qa):
        self.client.login(username="clinician", password="SecurePass123!")

        response = self.client.post(
            reverse("chat"),
            {"message": "<script>alert(1)</script>"},
            follow=True,
        )

        html = response.content.decode()

        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertTrue(ChatMessage.objects.filter(role="assistant", text="Structured reply.").exists())

    def test_chat_page_loads_page_specific_script(self):
        self.client.login(username="clinician", password="SecurePass123!")

        response = self.client.get(reverse("chat"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "chat.js")

    @patch("medical_app.views.answer_question", return_value={"used_local_qa": False})
    @patch("medical_app.views.analyze_image_with_query", return_value="Structured reply.")
    def test_chat_prompt_uses_saved_profile_preferences(self, mock_ai, mock_local_qa):
        self.client.login(username="clinician", password="SecurePass123!")

        self.client.post(
            reverse("chat"),
            {"message": "I have chest tightness today"},
            follow=True,
        )

        prompt = mock_ai.call_args.kwargs["query"]
        self.assertIn("Respond in hindi.", prompt)
        self.assertIn("Escalate uncertainty carefully", prompt)
        self.assertIn("clinical tone", prompt)


class QARuntimeCacheTests(TestCase):
    def setUp(self):
        cache.clear()
        from . import qa_engine

        qa_engine._QA_RETRIEVER_CACHE.clear()
        qa_engine._RUNTIME_DB_RETRIEVER_CACHE.clear()
        ClinicalKnowledgeEntry.objects.create(
            title="Electrolyte support",
            input_text="muscle cramps and weakness after dehydration",
            target_condition="Electrolyte Imbalance",
            target_specialization="Internal Medicine",
            target_treatment="Use oral electrolyte solution and review hydration status.",
            quality_score=90,
            is_approved=True,
        )
        ClinicalKnowledgeEntry.objects.create(
            title="Migraine support",
            input_text="severe headache with light sensitivity and nausea",
            target_condition="Migraine",
            target_specialization="Neurology",
            target_treatment="Rest in a dark room and use clinician-approved pain relief.",
            quality_score=90,
            is_approved=True,
        )

    def test_runtime_db_qa_retriever_uses_warm_cache_without_repeat_queries(self):
        from . import qa_engine
        from .services.ai_configuration import get_ai_configuration

        get_ai_configuration()
        missing_model_path = Path("medical_app/ml_models/runtime-cache-missing.pkl")

        with self.assertNumQueries(2):
            first_result = qa_engine.answer_question(
                "muscle cramps and weakness",
                model_path=missing_model_path,
            )

        self.assertTrue(first_result["used_local_qa"])

        with self.assertNumQueries(0):
            second_result = qa_engine.answer_question(
                "muscle cramps and weakness",
                model_path=missing_model_path,
            )

        self.assertEqual(first_result["answer"], second_result["answer"])


class DashboardTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin_user = user_model.objects.create_user(
            username="admin_user",
            email="admin_user@example.com",
            first_name="Admin",
            last_name="Manager",
            password="SecurePass123!",
            is_staff=True,
        )
        UserProfile.objects.update_or_create(
            user=self.admin_user,
            defaults={
                "mobile_number": "9999999999",
                "training_console_enabled": True,
            },
        )
        LoginActivity.objects.create(
            user=self.admin_user,
            session_key="abc123",
            ip_address="127.0.0.1",
            location_label="Local development machine",
            device_name="Windows desktop",
            browser_name="Google Chrome",
            is_active=True,
        )
        self.member_user = user_model.objects.create_user(
            username="member_user",
            email="member@example.com",
            first_name="Member",
            last_name="Viewer",
            password="SecurePass123!",
        )
        UserProfile.objects.update_or_create(
            user=self.member_user,
            defaults={"mobile_number": "8887776665"},
        )
        self.patient_owner = user_model.objects.create_user(
            username="case_owner",
            email="case_owner@example.com",
            first_name="Case",
            last_name="Owner",
            password="SecurePass123!",
        )
        analysis = MedicalAnalysis.objects.create(
            user=self.patient_owner,
            title="Eye Comfort Review",
            predicted_condition="Visual review suggested",
            report_text="Eye redness and irritation were noted after dust exposure.",
            risk_level="Low",
        )
        TreatmentEntry.objects.create(
            analysis=analysis,
            doctor_name="Dr. Private Detail",
            doctor_id="DOC-909",
            specialization="Eye Specialist",
            contact_details="555-9090",
            treatment_notes=(
                "Rinse the eye gently with sterile saline and use lubricating drops twice daily. "
                "Avoid rubbing the eye and reduce screen exposure for the next 48 hours. "
                "Return for review if pain or discharge worsens."
            ),
            added_by=self.admin_user,
        )

    def test_dashboard_requires_login(self):
        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_staff_dashboard_shows_user_management(self):
        self.client.login(username="admin_user", password="SecurePass123!")

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "User Management")
        self.assertContains(response, "Admin Manager")
        self.assertContains(response, "Live Training Status")
        self.assertContains(response, "Train Now")
        self.assertContains(response, "Open Training Center")
        self.assertContains(response, "Download Sample ZIP")
        self.assertContains(response, "Download Import Template")

    def test_member_dashboard_shows_shared_treatment_summary_without_private_details(self):
        self.client.login(username="member_user", password="SecurePass123!")

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Treatment Knowledge Feed")
        self.assertContains(response, "Eye Specialist")
        self.assertContains(response, "Rinse the eye gently with sterile saline")
        self.assertNotContains(response, "Dr. Private Detail")
        self.assertNotContains(response, "DOC-909")
        self.assertNotContains(response, "555-9090")
        self.assertNotContains(response, "Live Training Status")
        self.assertNotContains(response, "Download Import Template")

    def test_dashboard_page_loads_page_specific_script(self):
        self.client.login(username="admin_user", password="SecurePass123!")

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "dashboard.js")
        self.assertContains(response, "Quick Actions")
        self.assertContains(response, "AI Insights")
        self.assertContains(response, "Analytics")


class HistoryWorkspaceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = user_model.objects.create_user(
            username="history_user",
            email="history@example.com",
            password="SecurePass123!",
        )
        UserProfile.objects.update_or_create(
            user=self.user,
            defaults={"mobile_number": "7777777777"},
        )
        self.analysis = MedicalAnalysis.objects.create(
            user=self.user,
            title="Timeline Case",
            symptoms_text="Dry cough, chest heaviness, and mild fever",
            report_text="Inflammation markers are moderately elevated.",
            ai_summary="Possible respiratory inflammation with follow-up required.",
            predicted_condition="Respiratory",
            risk_level="Medium",
            progression_status="Improved",
            disease_percentage=35,
        )
        TreatmentEntry.objects.create(
            analysis=self.analysis,
            doctor_name="Dr. Lane",
            doctor_id="DOC-301",
            specialization="Pulmonology",
            treatment_notes="Continue inhaler support and monitor oxygen saturation.",
            added_by=self.user,
        )
        from .models import ChatSession

        self.session = ChatSession.objects.create(user=self.user)
        ChatMessage.objects.create(session=self.session, role="user", text="Can I continue work?")
        ChatMessage.objects.create(session=self.session, role="assistant", text="Take rest and monitor symptoms.")

    def test_history_page_shows_timeline_filters_and_detail_panel(self):
        self.client.login(username="history_user", password="SecurePass123!")

        response = self.client.get(reverse("history"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Filter + Search")
        self.assertContains(response, "Timeline")
        self.assertContains(response, "Timeline Case")
        self.assertContains(response, "Detailed View")
        self.assertContains(response, "View Record")


class ClinicalAnalysisTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = user_model.objects.create_user(
            username="doctor_user",
            email="doctor@example.com",
            first_name="Doctor",
            last_name="Tester",
            password="SecurePass123!",
        )
        UserProfile.objects.update_or_create(
            user=self.user,
            defaults={"mobile_number": "9998887776"},
        )

    @patch("medical_app.views.text_to_speech_with_edge")
    @patch("medical_app.views.analyze_image_with_query", return_value="Structured clinical summary.")
    def test_index_post_creates_medical_analysis_record(self, mock_ai, mock_tts):
        self.client.login(username="doctor_user", password="SecurePass123!")

        response = self.client.post(
            reverse("index"),
            {
                "symptoms": "Persistent cough with mild fever for three days",
                "report_notes": "Inflammation markers are elevated.",
                "language": "english",
            },
            follow=True,
        )

        analysis = MedicalAnalysis.objects.get(user=self.user)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Latest Clinical Record Saved")
        self.assertEqual(analysis.predicted_condition, "Infection")
        self.assertEqual(analysis.risk_level, "Medium")
        self.assertEqual(analysis.ai_summary, "Structured clinical summary.")
        mock_tts.assert_called_once()

    @patch("medical_app.views.text_to_speech_with_edge")
    @patch("medical_app.views.analyze_image_with_query", return_value="Structured disease comparison summary.")
    def test_index_can_compare_previous_and_current_reports_with_percentage_chart(self, mock_ai, mock_tts):
        self.client.login(username="doctor_user", password="SecurePass123!")

        response = self.client.post(
            reverse("index"),
            {
                "report_notes": "Current report shows disease burden at 30% with improved response to treatment.",
                "previous_report_notes": "Previous report recorded disease burden at 80% before treatment was started.",
                "language": "english",
            },
            follow=True,
        )

        analysis = MedicalAnalysis.objects.get(user=self.user)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Disease Burden Comparison")
        self.assertContains(response, "Reduced")
        self.assertContains(response, "Remaining")
        self.assertEqual(analysis.disease_percentage, 30.0)
        self.assertEqual(analysis.previous_disease_percentage, 80.0)
        self.assertEqual(analysis.percentage_reduced, 50.0)
        self.assertEqual(analysis.percentage_remaining, 30.0)
        self.assertEqual(analysis.progression_status, "Improved")
        mock_tts.assert_called_once()

    def test_index_page_loads_upload_script(self):
        response = self.client.get(reverse("index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "upload.js")

    @patch("medical_app.views.text_to_speech_with_edge")
    @patch("medical_app.views.analyze_image_with_query", return_value="Structured clinical summary.")
    def test_index_uses_site_language_for_response_and_audio(self, mock_ai, mock_tts):
        session = self.client.session
        session[SITE_LANGUAGE_SESSION_KEY] = "hindi"
        session.save()

        response = self.client.post(
            reverse("index"),
            {
                "symptoms": "Persistent cough with mild fever for three days",
                "report_notes": "Inflammation markers are elevated.",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        prompt = mock_ai.call_args.kwargs["query"]
        self.assertIn("Respond in hindi.", prompt)
        self.assertEqual(mock_tts.call_args.kwargs["language"], "hindi")

    @patch(
        "medical_app.services.analysis.analyze_image_record",
        return_value={
            "predicted_condition": "General review required",
            "confidence_score": 0,
            "model_source": "heuristic",
        },
    )
    @patch("medical_app.services.analysis.analyze_report_text")
    @patch("medical_app.views.analyze_image_with_query", return_value="Structured report summary.")
    def test_report_workspace_reuses_cached_report_analysis_for_identical_input(
        self,
        mock_ai,
        mock_report_analysis,
        mock_image_insights,
    ):
        self.user.profile.voice_summary_enabled = False
        self.user.profile.save(update_fields=["voice_summary_enabled", "updated_at"])
        mock_report_analysis.return_value = {
            "predicted_condition": "Infection",
            "detected_conditions_count": 1,
            "risk_level": "Medium",
            "confidence_score": 0.74,
            "disease_percentage": 25.0,
            "model_source": "heuristic",
        }
        self.client.login(username="doctor_user", password="SecurePass123!")

        response = self.client.post(
            reverse("report_intake"),
            {
                "report_notes": "Current report shows disease burden at 25% with improved response.",
                "language": "english",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_report_analysis.call_count, 1)

    @patch("medical_app.views.text_to_speech_with_edge")
    @patch("medical_app.views.analyze_image_with_query", return_value="Structured report summary.")
    def test_report_workspace_can_compare_reports(self, mock_ai, mock_tts):
        self.client.login(username="doctor_user", password="SecurePass123!")

        response = self.client.post(
            reverse("report_intake"),
            {
                "report_notes": "Current report shows disease burden at 25% with improved response.",
                "previous_report_notes": "Previous report recorded disease burden at 70%.",
                "language": "english",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Disease Burden Comparison")
        self.assertContains(response, "Structured report summary.")
        mock_tts.assert_called_once()

    @patch("medical_app.views.text_to_speech_with_edge")
    @patch("medical_app.views.analyze_image_with_query", return_value="Structured clinical summary.")
    def test_index_skips_voice_generation_when_user_disables_voice_summary(self, mock_ai, mock_tts):
        self.user.profile.voice_summary_enabled = False
        self.user.profile.save(update_fields=["voice_summary_enabled"])
        self.client.login(username="doctor_user", password="SecurePass123!")

        response = self.client.post(
            reverse("index"),
            {
                "symptoms": "Persistent cough with mild fever for three days",
                "report_notes": "Inflammation markers are elevated.",
                "language": "english",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        mock_tts.assert_not_called()

    def test_analysis_detail_requires_login(self):
        analysis = MedicalAnalysis.objects.create(
            user=self.user,
            title="Test Analysis",
            predicted_condition="General review required",
        )

        response = self.client.get(reverse("analysis_detail", args=[analysis.id]))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_analysis_detail_renders_uploaded_file_links(self):
        self.client.login(username="doctor_user", password="SecurePass123!")
        analysis = MedicalAnalysis.objects.create(
            user=self.user,
            title="Stored Files Analysis",
            predicted_condition="Respiratory",
            risk_level="Medium",
            report_file="medical_reports/test-report.txt",
            previous_report_file="medical_reports/test-previous.txt",
            medical_image="analysis_images/test-image.jpg",
        )

        response = self.client.get(reverse("analysis_detail", args=[analysis.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/media/medical_reports/test-report.txt")
        self.assertContains(response, "/media/medical_reports/test-previous.txt")
        self.assertContains(response, "/media/analysis_images/test-image.jpg")

    def test_treatment_entry_can_be_created_updated_and_deleted(self):
        self.client.login(username="doctor_user", password="SecurePass123!")
        analysis = MedicalAnalysis.objects.create(
            user=self.user,
            title="Respiratory Review",
            predicted_condition="Respiratory",
            risk_level="Medium",
        )

        create_response = self.client.post(
            reverse("analysis_detail", args=[analysis.id]),
            {
                "doctor_name": "Dr. John Carter",
                "doctor_id": "DOC-101",
                "specialization": "Pulmonology",
                "contact_details": "555-1010",
                "treatment_notes": "Start inhaler support and review in 48 hours.",
            },
            follow=True,
        )

        treatment_entry = TreatmentEntry.objects.get(analysis=analysis)
        training_record = TreatmentTrainingRecord.objects.get(treatment=treatment_entry)

        self.assertEqual(create_response.status_code, 200)
        self.assertContains(
            create_response,
            "Treatment entry saved successfully and synced to the ML training dataset.",
        )
        self.assertEqual(training_record.target_condition, "Respiratory")
        self.assertEqual(training_record.target_specialization, "Pulmonology")
        self.assertGreater(training_record.quality_score, 0)

        edit_response = self.client.post(
            reverse("treatment_entry_edit", args=[analysis.id, treatment_entry.id]),
            {
                "doctor_name": "Dr. John Carter",
                "doctor_id": "DOC-101",
                "specialization": "Pulmonology",
                "contact_details": "555-1010",
                "treatment_notes": "Updated follow-up after inhaler review.",
            },
            follow=True,
        )

        treatment_entry.refresh_from_db()
        training_record.refresh_from_db()
        self.assertEqual(edit_response.status_code, 200)
        self.assertEqual(treatment_entry.treatment_notes, "Updated follow-up after inhaler review.")
        self.assertEqual(training_record.target_treatment, "Updated follow-up after inhaler review.")

        delete_response = self.client.post(
            reverse("treatment_entry_delete", args=[analysis.id, treatment_entry.id]),
            follow=True,
        )

        self.assertEqual(delete_response.status_code, 200)
        self.assertFalse(TreatmentEntry.objects.filter(id=treatment_entry.id).exists())
        self.assertFalse(TreatmentTrainingRecord.objects.filter(treatment_id=treatment_entry.id).exists())


class TrainingPipelineTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = user_model.objects.create_user(
            username="ml_user",
            email="ml@example.com",
            password="SecurePass123!",
        )

    def _create_reviewed_case(self, title, condition, symptoms_text, report_text, specialization, notes):
        analysis = MedicalAnalysis.objects.create(
            user=self.user,
            title=title,
            symptoms_text=symptoms_text,
            report_text=report_text,
            ai_summary=f"AI review for {condition}",
            predicted_condition=condition,
            risk_level="Medium",
            detected_conditions_count=1,
            model_source="heuristic",
        )
        return TreatmentEntry.objects.create(
            analysis=analysis,
            doctor_name="Dr. Review",
            doctor_id=f"DOC-{analysis.id}",
            specialization=specialization,
            treatment_notes=notes,
            added_by=self.user,
        )

    def _create_condition_series(self, condition, specialization, symptom_prefix, report_prefix, note_prefix, count):
        for index in range(count):
            self._create_reviewed_case(
                f"{condition} case {index}",
                condition,
                f"{symptom_prefix} {index}",
                f"{report_prefix} {index}",
                specialization,
                f"{note_prefix} {index}",
            )

    def test_export_training_dataset_command_writes_jsonl(self):
        self._create_reviewed_case(
            "Respiratory case",
            "Respiratory",
            "Persistent cough and wheeze",
            "Bronchial inflammation noted in report",
            "Pulmonology",
            "Start bronchodilator and follow-up review.",
        )

        output_path = Path("medical_app") / "ml_models" / f"test-training-{uuid.uuid4().hex}.jsonl"
        try:
            call_command("export_training_dataset", output=str(output_path))

            self.assertTrue(output_path.exists())
            self.assertIn("Respiratory", output_path.read_text(encoding="utf-8"))
        finally:
            if output_path.exists():
                output_path.unlink()

    def test_generic_ai_condition_falls_back_to_doctor_specialization(self):
        treatment = self._create_reviewed_case(
            "Eye case",
            "Visual review suggested",
            "",
            "Eye irritation with redness",
            "Eye Specialist",
            "Clean the eye and monitor irritation.",
        )

        training_record = TreatmentTrainingRecord.objects.get(treatment=treatment)

        self.assertEqual(training_record.target_condition, "Eye Specialist")
        self.assertIn("fell back to doctor specialization", training_record.review_notes)

    def test_train_condition_model_creates_model_used_by_analysis_engine(self):
        self._create_condition_series(
            "Respiratory",
            "Pulmonology",
            "Persistent cough and wheeze",
            "Bronchial inflammation and asthma concern",
            "Start bronchodilator and inhaler support.",
            3,
        )
        self._create_condition_series(
            "Infection",
            "Internal Medicine",
            "Fever with throat pain",
            "Bacterial infection markers elevated",
            "Start antibiotic review and hydration plan.",
            3,
        )

        model_path = Path("medical_app") / "ml_models" / f"test-report-classifier-{uuid.uuid4().hex}.pkl"
        metrics_path = Path("medical_app") / "ml_models" / f"test-report-metrics-{uuid.uuid4().hex}.json"
        summary_path = Path("medical_app") / "ml_models" / f"test-report-summary-{uuid.uuid4().hex}.json"
        try:
            call_command(
                "train_condition_model",
                output=str(model_path),
                metrics_output=str(metrics_path),
                summary_output=str(summary_path),
                minimum_records=6,
            )

            self.assertTrue(model_path.exists())
            self.assertTrue(metrics_path.exists())
            self.assertTrue(summary_path.exists())

            metrics = load_evaluation_report(metrics_path)
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(metrics["total_records"], 6)
            self.assertEqual(metrics["train_count"], 4)
            self.assertEqual(metrics["test_count"], 2)
            self.assertIn("macro_f1", metrics)
            self.assertEqual(summary["filtered_record_count"], 6)
            self.assertEqual(summary["duplicates_removed"], 0)

            with patch("medical_app.analysis_engine.REPORT_MODEL_PATH", model_path):
                result = analyze_report_text("Patient has persistent cough and wheeze with bronchial irritation.")

            self.assertEqual(result["model_source"], "trained-model")
            self.assertEqual(result["predicted_condition"], "Respiratory")
        finally:
            if model_path.exists():
                model_path.unlink()
            if metrics_path.exists():
                metrics_path.unlink()
            if summary_path.exists():
                summary_path.unlink()

    def test_train_qa_ranker_command_writes_runtime_artifacts(self):
        dataset_dir = make_scratch_dir("dataset-train-qa")
        try:
            write_csv_dataset(
                dataset_dir / "medical_data.csv",
                ["Patient_Problem", "Disease", "Prescription"],
                [
                    {
                        "Patient_Problem": "Constant fatigue and muscle weakness",
                        "Disease": "Chronic Fatigue Syndrome",
                        "Prescription": "graded exercise",
                    }
                ],
            )
            write_csv_dataset(
                dataset_dir / "Diseases_Symptoms.csv",
                ["Name", "Symptoms", "Treatments", "Disease_Code", "Contagious", "Chronic"],
                [
                    {
                        "Name": "Migraine",
                        "Symptoms": "head pain with light sensitivity",
                        "Treatments": "rest in a dark room",
                        "Disease_Code": "D2",
                        "Contagious": "False",
                        "Chronic": "True",
                    }
                ],
            )
            write_csv_dataset(
                dataset_dir / "medical_question_answer_dataset_50000.csv",
                ["ID", "Symptoms/Question", "Disease Prediction", "Recommended Medicines", "Advice"],
                [
                    {
                        "ID": "1",
                        "Symptoms/Question": "muscle cramps and weakness",
                        "Disease Prediction": "Electrolyte Imbalance",
                        "Recommended Medicines": "Electrolyte solution",
                        "Advice": "Stay hydrated",
                    },
                    {
                        "ID": "2",
                        "Symptoms/Question": "severe headache with light sensitivity",
                        "Disease Prediction": "Migraine",
                        "Recommended Medicines": "Pain relievers",
                        "Advice": "Rest in a quiet room",
                    },
                ],
            )

            qa_model_path = Path("medical_app") / "ml_models" / f"test-qa-ranker-{uuid.uuid4().hex}.pkl"
            qa_corpus_path = Path("medical_app") / "ml_models" / f"test-qa-corpus-{uuid.uuid4().hex}.jsonl"
            qa_metrics_path = Path("medical_app") / "ml_models" / f"test-qa-metrics-{uuid.uuid4().hex}.json"
            qa_summary_path = Path("medical_app") / "ml_models" / f"test-qa-summary-{uuid.uuid4().hex}.json"

            try:
                call_command(
                    "train_qa_ranker",
                    datasets_dir=str(dataset_dir),
                    dedupe=True,
                    output=str(qa_model_path),
                    corpus_output=str(qa_corpus_path),
                    metrics_output=str(qa_metrics_path),
                    summary_output=str(qa_summary_path),
                )

                self.assertTrue(qa_model_path.exists())
                self.assertTrue(qa_corpus_path.exists())
                self.assertTrue(qa_metrics_path.exists())
                self.assertTrue(qa_summary_path.exists())

                qa_metrics = json.loads(qa_metrics_path.read_text(encoding="utf-8"))
                qa_summary = json.loads(qa_summary_path.read_text(encoding="utf-8"))
                self.assertEqual(qa_metrics["corpus_count"], 4)
                self.assertIn("hit_rate_at_1", qa_metrics)
                self.assertEqual(qa_summary["total_entries_after_dedupe"], 4)
            finally:
                for artifact_path in (qa_model_path, qa_corpus_path, qa_metrics_path, qa_summary_path):
                    if artifact_path.exists():
                        artifact_path.unlink()
        finally:
            cleanup_scratch_dir(dataset_dir)


class AdminKnowledgePipelineTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = user_model.objects.create_user(
            username="knowledge_admin",
            email="knowledge_admin@example.com",
            password="SecurePass123!",
            is_staff=True,
            is_superuser=True,
        )
        self.staff_user = user_model.objects.create_user(
            username="limited_staff",
            email="limited_staff@example.com",
            password="SecurePass123!",
            is_staff=True,
        )

    def test_bulk_training_upload_creates_knowledge_entries_and_updates_pending_queue(self):
        from .services.knowledge_base import process_training_dataset_upload

        csv_content = "\n".join(
            [
                "title,input_text,target_condition,target_specialization,target_treatment,quality_score,is_approved",
                "Respiratory Intake,patient has persistent cough and wheeze,Respiratory,Pulmonology,Start inhaler and pulmonary follow-up,92,true",
                "Infection Intake,fever with sore throat and fatigue,Infection,Internal Medicine,Hydration and antibiotic review,88,true",
            ]
        )
        upload = TrainingDatasetUpload.objects.create(
            title="Admin knowledge batch",
            source_label="semester-demo-upload",
            dataset_file=SimpleUploadedFile(
                "knowledge-upload.csv",
                csv_content.encode("utf-8"),
                content_type="text/csv",
            ),
            created_by=self.user,
        )

        result = process_training_dataset_upload(upload, processed_by=self.user)
        upload.refresh_from_db()
        configuration = AIModelConfiguration.objects.get(configuration_key="default")

        self.assertEqual(result["created_rows"], 2)
        self.assertEqual(upload.status, TrainingDatasetUpload.STATUS_PROCESSED)
        self.assertEqual(ClinicalKnowledgeEntry.objects.count(), 2)
        self.assertEqual(configuration.pending_training_records, 2)

    def test_admin_template_download_requires_developer_access_and_returns_csv(self):
        response = self.client.get("/admin/medical_app/trainingdatasetupload/download-template/")
        self.assertEqual(response.status_code, 302)

        self.client.login(username="knowledge_admin", password="SecurePass123!")
        response = self.client.get("/admin/medical_app/trainingdatasetupload/download-template/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("attachment; filename=", response["Content-Disposition"])
        self.assertContains(
            response,
            "title,input_text,target_condition,target_specialization,target_treatment,quality_score,is_approved,ai_context,review_notes",
        )

    def test_admin_template_download_denies_non_developer_staff(self):
        self.client.login(username="limited_staff", password="SecurePass123!")

        response = self.client.get("/admin/medical_app/trainingdatasetupload/download-template/")

        self.assertEqual(response.status_code, 403)

    def test_training_control_center_requires_developer_access_and_shows_secure_controls(self):
        response = self.client.get(reverse("training_control"))
        self.assertEqual(response.status_code, 302)

        self.client.login(username="knowledge_admin", password="SecurePass123!")
        response = self.client.get(reverse("training_control"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Developer Training Control")
        self.assertContains(response, "Train Now")
        self.assertContains(response, "Download Sample ZIP")
        self.assertContains(response, "run_training_worker --continuous")
        self.assertContains(response, "training_admin.js")

    def test_training_control_center_denies_non_developer_staff(self):
        self.client.login(username="limited_staff", password="SecurePass123!")

        response = self.client.get(reverse("training_control"))

        self.assertEqual(response.status_code, 403)

    def test_dashboard_hides_training_widget_for_non_developer_staff(self):
        self.client.login(username="limited_staff", password="SecurePass123!")

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Live Training Status")

    @patch("medical_app.views.enqueue_ai_model_refresh")
    def test_training_control_train_now_queues_refresh_for_staff(self, mock_enqueue):
        mock_enqueue.return_value = (
            AITrainingRun(
                id=77,
                version_label="vtest",
                status=AITrainingRun.STATUS_QUEUED,
            ),
            True,
        )
        self.client.login(username="knowledge_admin", password="SecurePass123!")

        response = self.client.post(
            reverse("training_control_train_now"),
            {"next": reverse("training_control")},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "queued successfully")
        mock_enqueue.assert_called_once()
        _, enqueue_kwargs = mock_enqueue.call_args
        self.assertEqual(enqueue_kwargs["triggered_by"], self.user)
        self.assertEqual(enqueue_kwargs["trigger_type"], "manual")

    def test_queue_training_refresh_enqueues_background_job_when_threshold_is_reached(self):
        from .services.ai_configuration import get_ai_configuration
        from .services.retraining import queue_training_refresh

        configuration = get_ai_configuration()
        configuration.auto_retrain_enabled = True
        configuration.auto_retrain_after_manual_entry = True
        configuration.min_new_records_for_retrain = 1
        configuration.retrain_cooldown_minutes = 0
        configuration.last_trained_at = None
        configuration.pending_training_records = 0
        configuration.last_training_status = "idle"
        configuration.save(
            update_fields=[
                "auto_retrain_enabled",
                "auto_retrain_after_manual_entry",
                "min_new_records_for_retrain",
                "retrain_cooldown_minutes",
                "last_trained_at",
                "pending_training_records",
                "last_training_status",
                "updated_at",
            ]
        )

        pending_records = queue_training_refresh(
            record_count=1,
            trigger_type="manual_entry",
            reason="Queue threshold test",
        )

        configuration.refresh_from_db()
        queued_run = AITrainingRun.objects.get()

        self.assertEqual(pending_records, 1)
        self.assertEqual(queued_run.status, AITrainingRun.STATUS_QUEUED)
        self.assertEqual(configuration.last_training_status, "queued")

    @patch("medical_app.services.retraining._safe_load_json")
    @patch("medical_app.services.retraining.call_command")
    def test_process_next_training_run_executes_queued_job(self, mock_call_command, mock_safe_load_json):
        from .services.ai_configuration import get_ai_configuration
        from .services.retraining import enqueue_ai_model_refresh, process_next_training_run

        mock_safe_load_json.side_effect = [
            {
                "accuracy_percent": 70.0,
                "macro_f1": 0.59,
                "weighted_f1": 0.68,
                "total_records": 31,
            },
            {
                "hit_rate_at_1_percent": 54.0,
                "average_score": 0.81,
                "corpus_count": 17,
            },
        ]

        configuration = get_ai_configuration()
        configuration.pending_training_records = 4
        configuration.last_training_status = "idle"
        configuration.last_trained_at = None
        configuration.save(
            update_fields=[
                "pending_training_records",
                "last_training_status",
                "last_trained_at",
                "updated_at",
            ]
        )

        queued_run, created = enqueue_ai_model_refresh(
            run_reason="Queued developer trigger",
            configuration=configuration,
            triggered_by=self.user,
            trigger_type="manual",
        )

        self.assertTrue(created)
        self.assertEqual(queued_run.status, AITrainingRun.STATUS_QUEUED)

        processed_run = process_next_training_run()
        configuration.refresh_from_db()
        queued_run.refresh_from_db()

        self.assertEqual(mock_call_command.call_count, 2)
        self.assertEqual(processed_run.pk, queued_run.pk)
        self.assertEqual(queued_run.status, AITrainingRun.STATUS_SUCCESS)
        self.assertTrue(queued_run.is_active_version)
        self.assertEqual(configuration.pending_training_records, 0)
        self.assertEqual(configuration.last_training_status, "success")

    @patch("medical_app.management.commands.run_training_worker.process_next_training_run")
    def test_run_training_worker_once_reports_processed_job(self, mock_process_next_training_run):
        training_run = AITrainingRun.objects.create(
            version_label="vworker",
            run_reason="Worker test",
            status=AITrainingRun.STATUS_SUCCESS,
            trigger_type="manual",
        )
        mock_process_next_training_run.return_value = training_run

        output = StringIO()
        call_command("run_training_worker", "--once", stdout=output)

        command_output = output.getvalue()
        self.assertIn("Training worker started.", command_output)
        self.assertIn("Processed vworker: success.", command_output)
        self.assertIn("processing 1 job(s)", command_output)

    @patch("medical_app.services.retraining._safe_load_json")
    @patch("medical_app.services.retraining.call_command")
    def test_refresh_ai_models_creates_versioned_training_run(self, mock_call_command, mock_safe_load_json):
        from .services.ai_configuration import get_ai_configuration
        from .services.retraining import refresh_ai_models

        mock_safe_load_json.side_effect = [
            {
                "accuracy_percent": 73.4,
                "macro_f1": 0.61,
                "weighted_f1": 0.69,
                "total_records": 42,
            },
            {
                "hit_rate_at_1_percent": 58.0,
                "average_score": 0.87,
                "corpus_count": 19,
            },
        ]

        configuration = get_ai_configuration()
        configuration.pending_training_records = 7
        configuration.save(update_fields=["pending_training_records", "updated_at"])

        succeeded = refresh_ai_models(
            run_reason="Developer trigger test",
            configuration=configuration,
            triggered_by=self.user,
            trigger_type="manual",
        )

        self.assertTrue(succeeded)
        self.assertEqual(mock_call_command.call_count, 2)

        run = AITrainingRun.objects.get()
        self.assertEqual(run.status, AITrainingRun.STATUS_SUCCESS)
        self.assertEqual(run.triggered_by, self.user)
        self.assertEqual(run.pending_record_snapshot, 7)
        self.assertTrue(run.is_active_version)
        self.assertEqual(run.classifier_record_count, 42)
        self.assertEqual(run.qa_corpus_count, 19)

        configuration.refresh_from_db()
        self.assertEqual(configuration.pending_training_records, 0)
        self.assertEqual(configuration.last_training_status, "success")

    def test_training_control_upload_returns_warning_preview_json(self):
        self.client.login(username="knowledge_admin", password="SecurePass123!")

        csv_content = "\n".join(
            [
                "title,input_text,target_condition,target_specialization,target_treatment,quality_score,is_approved",
                "Valid respiratory row,persistent cough and wheeze,Respiratory,Pulmonology,Review inhaler use,90,true",
                "Broken row,headache with nausea,,Neurology,Review migraine support,80,true",
            ]
        )

        response = self.client.post(
            reverse("training_control_upload"),
            {
                "title": "Warning preview batch",
                "source_label": "developer-preview",
                "auto_retrain_requested": "true",
                "dataset_file": SimpleUploadedFile(
                    "warning-preview.csv",
                    csv_content.encode("utf-8"),
                    content_type="text/csv",
                ),
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        payload = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["created_rows"], 1)
        self.assertEqual(payload["skipped_rows"], 1)
        self.assertEqual(payload["warning_count"], 1)
        self.assertIn("Row 3", payload["warning_preview"][0])
        self.assertTrue(payload["error_report_url"])
        self.assertEqual(ClinicalKnowledgeEntry.objects.count(), 1)

        upload = TrainingDatasetUpload.objects.get(title="Warning preview batch")
        self.assertTrue(bool(upload.error_report_file))
        self.assertEqual(upload.skipped_rows, 1)

    def test_training_control_sample_zip_requires_developer_access_and_contains_multiple_examples(self):
        response = self.client.get(reverse("training_control_sample_zip"))
        self.assertEqual(response.status_code, 302)

        self.client.login(username="knowledge_admin", password="SecurePass123!")
        response = self.client.get(reverse("training_control_sample_zip"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/zip")

        with zipfile.ZipFile(BytesIO(response.content)) as archive:
            names = set(archive.namelist())

        self.assertIn("clinical_knowledge_template.csv", names)
        self.assertIn("respiratory_cases.csv", names)
        self.assertIn("multi_specialty_cases.csv", names)
        self.assertIn("warning_preview_examples.csv", names)
        self.assertIn("README.txt", names)

    def test_training_control_sample_zip_denies_non_developer_staff(self):
        self.client.login(username="limited_staff", password="SecurePass123!")

        response = self.client.get(reverse("training_control_sample_zip"))

        self.assertEqual(response.status_code, 403)

    def test_train_condition_model_uses_admin_knowledge_entries(self):
        for condition, specialization, note_prefix in (
            ("Respiratory", "Pulmonology", "Provide inhaler support"),
            ("Respiratory", "Pulmonology", "Review breathing pattern"),
            ("Respiratory", "Pulmonology", "Monitor wheeze severity"),
            ("Infection", "Internal Medicine", "Start hydration plan"),
            ("Infection", "Internal Medicine", "Review antibiotic need"),
            ("Infection", "Internal Medicine", "Monitor fever and throat pain"),
        ):
            ClinicalKnowledgeEntry.objects.create(
                title=f"{condition} knowledge",
                input_text=f"{condition} case details {uuid.uuid4().hex}",
                ai_context=f"Clinical context for {condition}",
                target_condition=condition,
                target_specialization=specialization,
                target_treatment=note_prefix,
                quality_score=90,
                is_approved=True,
                created_by=self.user,
            )

        model_path = Path("medical_app") / "ml_models" / f"test-admin-report-classifier-{uuid.uuid4().hex}.pkl"
        metrics_path = Path("medical_app") / "ml_models" / f"test-admin-report-metrics-{uuid.uuid4().hex}.json"
        summary_path = Path("medical_app") / "ml_models" / f"test-admin-report-summary-{uuid.uuid4().hex}.json"

        try:
            call_command(
                "train_condition_model",
                output=str(model_path),
                metrics_output=str(metrics_path),
                summary_output=str(summary_path),
                minimum_records=6,
                source_types="admin_manual,admin_bulk_upload",
            )

            metrics = load_evaluation_report(metrics_path)
            summary = json.loads(summary_path.read_text(encoding="utf-8"))

            self.assertTrue(model_path.exists())
            self.assertEqual(metrics["total_records"], 6)
            self.assertEqual(summary["filtered_record_count"], 6)
            self.assertEqual(summary["source_distribution"]["admin_manual"], 6)
        finally:
            for artifact_path in (model_path, metrics_path, summary_path):
                if artifact_path.exists():
                    artifact_path.unlink()

    def test_train_qa_ranker_uses_approved_admin_knowledge_entries(self):
        ClinicalKnowledgeEntry.objects.create(
            title="Electrolyte support",
            input_text="muscle cramps and weakness after dehydration",
            ai_context="patient reports recent fluid loss",
            target_condition="Electrolyte Imbalance",
            target_specialization="Internal Medicine",
            target_treatment="Use oral electrolyte solution and review hydration status.",
            quality_score=91,
            is_approved=True,
            created_by=self.user,
        )
        ClinicalKnowledgeEntry.objects.create(
            title="Migraine support",
            input_text="severe headache with light sensitivity and nausea",
            ai_context="classic migraine pattern",
            target_condition="Migraine",
            target_specialization="Neurology",
            target_treatment="Rest in a dark room and use clinician-approved pain relief.",
            quality_score=90,
            is_approved=True,
            created_by=self.user,
        )

        dataset_dir = make_scratch_dir("admin-knowledge-qa")
        qa_model_path = Path("medical_app") / "ml_models" / f"test-admin-qa-ranker-{uuid.uuid4().hex}.pkl"
        qa_corpus_path = Path("medical_app") / "ml_models" / f"test-admin-qa-corpus-{uuid.uuid4().hex}.jsonl"
        qa_metrics_path = Path("medical_app") / "ml_models" / f"test-admin-qa-metrics-{uuid.uuid4().hex}.json"
        qa_summary_path = Path("medical_app") / "ml_models" / f"test-admin-qa-summary-{uuid.uuid4().hex}.json"

        try:
            call_command(
                "train_qa_ranker",
                datasets_dir=str(dataset_dir),
                dedupe=True,
                output=str(qa_model_path),
                corpus_output=str(qa_corpus_path),
                metrics_output=str(qa_metrics_path),
                summary_output=str(qa_summary_path),
            )

            qa_metrics = json.loads(qa_metrics_path.read_text(encoding="utf-8"))
            qa_summary = json.loads(qa_summary_path.read_text(encoding="utf-8"))

            self.assertTrue(qa_model_path.exists())
            self.assertEqual(qa_metrics["corpus_count"], 2)
            self.assertEqual(qa_summary["approved_db"]["knowledge_entries"], 2)
            self.assertEqual(qa_summary["total_entries_after_dedupe"], 2)
        finally:
            cleanup_scratch_dir(dataset_dir)
            for artifact_path in (qa_model_path, qa_corpus_path, qa_metrics_path, qa_summary_path):
                if artifact_path.exists():
                    artifact_path.unlink()


class BootstrapDefaultsCommandTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_bootstrap_defaults_command_is_idempotent(self):
        FeaturedImage.objects.all().delete()
        user_model.objects.filter(username="Admin").delete()

        call_command("bootstrap_defaults")
        call_command("bootstrap_defaults")

        self.assertEqual(FeaturedImage.objects.count(), 3)
        self.assertFalse(user_model.objects.filter(username="Admin").exists())

    @override_settings(
        CREATE_DEMO_ADMIN=True,
        DEMO_ADMIN_USERNAME="admin1",
        DEMO_ADMIN_EMAIL="admin1@example.com",
        DEMO_ADMIN_PASSWORD="admin123",
    )
    def test_bootstrap_defaults_can_create_demo_admin_when_enabled(self):
        user_model.objects.filter(username="admin1").delete()

        call_command("bootstrap_defaults")

        admin_user = user_model.objects.get(username="admin1")
        self.assertTrue(admin_user.is_superuser)
        self.assertTrue(admin_user.is_staff)
        self.assertTrue(admin_user.check_password("admin123"))
        self.assertTrue(admin_user.profile.training_console_enabled)

    @override_settings(
        DJANGO_SITE_DOMAIN="prod.example.com",
        DJANGO_SITE_NAME="AI Medical Assistant Production",
    )
    def test_bootstrap_defaults_uses_configured_site_values(self):
        call_command("bootstrap_defaults")

        site = Site.objects.get(pk=1)
        self.assertEqual(site.domain, "prod.example.com")
        self.assertEqual(site.name, "AI Medical Assistant Production")


class MiddlewarePerformanceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = user_model.objects.create_user(
            username="perf_user",
            email="perf@example.com",
            password="SecurePass123!",
        )

    def test_repeat_authenticated_requests_do_not_rewrite_login_activity_immediately(self):
        self.client.login(username="perf_user", password="SecurePass123!")

        first_response = self.client.get(reverse("dashboard"))
        self.assertEqual(first_response.status_code, 200)

        activity = LoginActivity.objects.get(user=self.user)
        first_seen = activity.last_seen
        profile_updated_at = self.user.profile.updated_at

        second_response = self.client.get(reverse("dashboard"))
        self.assertEqual(second_response.status_code, 200)

        activity.refresh_from_db()
        self.user.profile.refresh_from_db()

        self.assertEqual(LoginActivity.objects.filter(user=self.user).count(), 1)
        self.assertEqual(activity.last_seen, first_seen)
        self.assertEqual(self.user.profile.updated_at, profile_updated_at)


class AccountSettingsTests(TestCase):
    def setUp(self):
        self.user = user_model.objects.create_user(
            username="patient_user",
            email="patient@example.com",
            first_name="Patient",
            last_name="User",
            password="SecurePass123!",
        )
        UserProfile.objects.update_or_create(
            user=self.user,
            defaults={"mobile_number": "8888888888"},
        )

    def test_account_settings_requires_login(self):
        response = self.client.get(reverse("change_credentials"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response.url)

    def test_account_settings_page_exposes_live_validation_hooks(self):
        self.client.login(username="patient_user", password="SecurePass123!")

        response = self.client.get(reverse("change_credentials"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-live-validate="email"')
        self.assertContains(response, 'data-live-validate="mobile"')
        self.assertContains(response, "AI Preferences")

    def test_user_can_update_profile(self):
        self.client.login(username="patient_user", password="SecurePass123!")

        response = self.client.post(
            reverse("change_credentials"),
            {
                "form_type": "profile",
                "profile-first_name": "Updated",
                "profile-last_name": "Member",
                "profile-email": "updated@example.com",
                "profile-mobile_number": "7777777777",
            },
            follow=True,
        )

        self.user.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.user.first_name, "Updated")
        self.assertEqual(self.user.email, "updated@example.com")
        self.assertEqual(self.user.profile.mobile_number, "7777777777")

    def test_profile_update_preserves_training_console_flag_when_field_is_not_exposed(self):
        self.user.profile.training_console_enabled = True
        self.user.profile.save(update_fields=["training_console_enabled", "updated_at"])
        self.client.login(username="patient_user", password="SecurePass123!")

        response = self.client.post(
            reverse("change_credentials"),
            {
                "form_type": "profile",
                "profile-first_name": "Patient",
                "profile-last_name": "User",
                "profile-email": "patient@example.com",
                "profile-mobile_number": "8888888888",
            },
            follow=True,
        )

        self.user.profile.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.user.profile.training_console_enabled)

    def test_user_can_update_medical_and_preference_fields(self):
        self.client.login(username="patient_user", password="SecurePass123!")

        response = self.client.post(
            reverse("change_credentials"),
            {
                "form_type": "profile",
                "profile-first_name": "Patient",
                "profile-last_name": "User",
                "profile-email": "patient@example.com",
                "profile-mobile_number": "8888888888",
                "profile-date_of_birth": "1998-02-10",
                "profile-gender": "female",
                "profile-blood_group": "O+",
                "profile-allergies": "Dust",
                "profile-chronic_conditions": "Asthma",
                "profile-current_medications": "Inhaler",
                "profile-emergency_contact": "Sam 9999999999",
                "profile-language_preference": "hindi",
                "profile-response_style": "clinical",
                "profile-ai_risk_preference": "conservative",
                "profile-notification_preference": "analysis_updates",
                "profile-privacy_mode": "private",
                "profile-performance_mode": "quality",
                "profile-voice_summary_enabled": "on",
                "profile-auto_compare_reports": "on",
            },
            follow=True,
        )

        self.user.refresh_from_db()
        self.user.profile.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(str(self.user.profile.date_of_birth), "1998-02-10")
        self.assertEqual(self.user.profile.blood_group, "O+")
        self.assertEqual(self.user.profile.language_preference, "hindi")
        self.assertEqual(self.user.profile.response_style, "clinical")
        self.assertEqual(self.user.profile.ai_risk_preference, "conservative")
        self.assertEqual(self.user.profile.privacy_mode, "private")

    def test_user_cannot_update_profile_with_invalid_email_or_mobile(self):
        self.client.login(username="patient_user", password="SecurePass123!")

        response = self.client.post(
            reverse("change_credentials"),
            {
                "form_type": "profile",
                "profile-first_name": "Patient",
                "profile-last_name": "User",
                "profile-email": "not-an-email",
                "profile-mobile_number": "12AB",
            },
        )

        self.user.refresh_from_db()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Enter a valid email ID.")
        self.assertContains(response, "Enter a valid mobile number with 10 to 15 digits.")
        self.assertEqual(self.user.email, "patient@example.com")
        self.assertEqual(self.user.profile.mobile_number, "8888888888")

    def test_user_can_update_password(self):
        self.client.login(username="patient_user", password="SecurePass123!")

        response = self.client.post(
            reverse("change_credentials"),
            {
                "form_type": "password",
                "password-old_password": "SecurePass123!",
                "password-new_password1": "NewSecurePass456!",
                "password-new_password2": "NewSecurePass456!",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            self.client.login(username="patient_user", password="NewSecurePass456!")
        )
