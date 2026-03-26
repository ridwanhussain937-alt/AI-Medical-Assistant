from django.core.cache import cache

from medical_app.models import AIModelConfiguration


AI_CONFIGURATION_CACHE_KEY = "medical_app.ai_model_configuration.default"
DEFAULT_MEDICAL_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"


def invalidate_ai_configuration_cache():
    cache.delete(AI_CONFIGURATION_CACHE_KEY)


def get_ai_configuration():
    configuration = cache.get(AI_CONFIGURATION_CACHE_KEY)
    if configuration is not None:
        return configuration

    configuration, _ = AIModelConfiguration.objects.get_or_create(
        configuration_key="default",
        defaults={
            "chat_model_name": DEFAULT_MEDICAL_MODEL,
            "analysis_model_name": DEFAULT_MEDICAL_MODEL,
        },
    )
    cache.set(AI_CONFIGURATION_CACHE_KEY, configuration, timeout=300)
    return configuration


def build_generation_settings(configuration=None):
    configuration = configuration or get_ai_configuration()
    return {
        "temperature": max(0.0, min(float(configuration.temperature), 2.0)),
        "top_p": max(0.0, min(float(configuration.top_p), 1.0)),
        "max_output_tokens": max(64, int(configuration.max_output_tokens or 900)),
    }


def get_chat_model_name(configuration=None):
    configuration = configuration or get_ai_configuration()
    return configuration.chat_model_name.strip() or DEFAULT_MEDICAL_MODEL


def get_analysis_model_name(configuration=None):
    configuration = configuration or get_ai_configuration()
    return configuration.analysis_model_name.strip() or DEFAULT_MEDICAL_MODEL


def get_system_prompt(configuration=None):
    configuration = configuration or get_ai_configuration()
    return (configuration.system_prompt or "").strip()


def get_classifier_training_options(configuration=None):
    configuration = configuration or get_ai_configuration()
    return {
        "minimum_class_occurrences": max(1, int(configuration.classifier_min_class_occurrences or 3)),
        "train_ratio": float(configuration.classifier_train_ratio or 0.8),
        "seed": int(configuration.random_seed or 42),
    }


def get_qa_training_options(configuration=None):
    configuration = configuration or get_ai_configuration()
    return {
        "minimum_score": float(configuration.qa_min_confidence or 0.2),
        "train_ratio": float(configuration.qa_train_ratio or 0.8),
        "seed": int(configuration.random_seed or 42),
    }
