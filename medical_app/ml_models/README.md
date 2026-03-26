Place optional trained model files in this directory to augment the heuristic classifier used by the application.

In production, the app can override this location with `DJANGO_MODEL_ARTIFACT_ROOT`, which is useful for persistent-disk deployments such as Render.

If a trained model returns a label that conflicts with clear keyword-based report evidence, the app keeps the heuristic result so stale or out-of-scope models do not destabilize the UI.

Supported file names:

- `report_classifier.pkl`
- `image_classifier.pkl`
- `report_classifier_metrics.json`
- `report_classifier_dataset_summary.json`
- `qa_ranker.pkl`
- `qa_corpus.jsonl`
- `qa_metrics.json`
- `qa_dataset_summary.json`

Useful management commands:

- `python manage.py import_external_datasets --datasets-dir <path> --replace --dedupe`
- `python manage.py sync_training_records`
- `python manage.py export_training_dataset --format jsonl`
- `python manage.py train_condition_model`
- `python manage.py train_qa_ranker --datasets-dir <path> --dedupe`
