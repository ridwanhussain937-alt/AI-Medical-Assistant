(function () {
    const uploadForm = document.getElementById("training-upload-form");
    if (!uploadForm) {
        return;
    }

    const submitButton = document.getElementById("training-upload-submit");
    const progressShell = document.getElementById("training-upload-progress");
    const progressFill = document.getElementById("training-upload-progress-fill");
    const progressValue = document.getElementById("training-upload-progress-value");
    const progressLabel = document.getElementById("training-upload-progress-label");
    const feedbackCard = document.getElementById("training-upload-feedback");
    const feedbackMessage = document.getElementById("training-upload-feedback-message");
    const warningList = document.getElementById("training-warning-list");
    const errorReportLinks = document.getElementById("training-error-report-links");
    const errorReportLink = document.getElementById("training-error-report-link");
    const resultStatus = document.getElementById("training-result-status");
    const resultCreated = document.getElementById("training-result-created");
    const resultSkipped = document.getElementById("training-result-skipped");
    const resultApproved = document.getElementById("training-result-approved");
    const csrfToken = uploadForm.querySelector("input[name='csrfmiddlewaretoken']")?.value || "";
    const defaultButtonLabel = submitButton ? submitButton.textContent : "Upload and Process";

    function toggleHidden(element, shouldHide) {
        if (!element) {
            return;
        }
        element.classList.toggle("is-hidden", shouldHide);
    }

    function updateProgress(percent, labelText) {
        if (!progressShell || !progressFill || !progressValue || !progressLabel) {
            return;
        }
        const safePercent = Math.max(0, Math.min(100, Math.round(percent)));
        toggleHidden(progressShell, false);
        progressFill.style.width = safePercent + "%";
        progressValue.textContent = safePercent + "%";
        progressLabel.textContent = labelText;
    }

    function fillWarnings(warnings, fallbackMessage) {
        if (!warningList) {
            return;
        }
        warningList.innerHTML = "";
        const entries = warnings && warnings.length ? warnings : [fallbackMessage];
        entries.forEach(function (warning) {
            const item = document.createElement("li");
            item.textContent = warning;
            warningList.appendChild(item);
        });
    }

    function renderFeedback(data, isError) {
        toggleHidden(feedbackCard, false);
        if (feedbackMessage) {
            feedbackMessage.textContent = data.message || (isError ? "Upload failed." : "Upload completed.");
        }
        if (resultStatus) {
            resultStatus.textContent = data.status_label || (isError ? "Failed" : "Processed");
        }
        if (resultCreated) {
            resultCreated.textContent = String(data.created_rows || 0);
        }
        if (resultSkipped) {
            resultSkipped.textContent = String(data.skipped_rows || 0);
        }
        if (resultApproved) {
            resultApproved.textContent = String(data.approved_created || 0);
        }

        fillWarnings(
            data.warning_preview || [],
            isError
                ? "The server returned an error while processing this upload."
                : "No row warnings were returned for this batch."
        );

        if (errorReportLinks && errorReportLink) {
            if (data.error_report_url) {
                errorReportLink.href = data.error_report_url;
                toggleHidden(errorReportLinks, false);
            } else {
                errorReportLink.href = "#";
                toggleHidden(errorReportLinks, true);
            }
        }
    }

    uploadForm.addEventListener("submit", function (event) {
        event.preventDefault();

        const fileField = uploadForm.querySelector("input[name='dataset_file']");
        if (!fileField || !fileField.files.length) {
            renderFeedback(
                {
                    message: "Select a CSV or ZIP file before uploading.",
                    status_label: "Missing file",
                },
                true
            );
            return;
        }

        const xhr = new XMLHttpRequest();
        const formData = new FormData(uploadForm);

        if (submitButton) {
            submitButton.disabled = true;
            submitButton.textContent = "Processing...";
        }
        toggleHidden(feedbackCard, true);
        updateProgress(4, "Starting secure upload...");

        xhr.open("POST", uploadForm.action);
        xhr.responseType = "json";
        xhr.setRequestHeader("X-Requested-With", "XMLHttpRequest");
        if (csrfToken) {
            xhr.setRequestHeader("X-CSRFToken", csrfToken);
        }

        xhr.upload.addEventListener("progress", function (progressEvent) {
            if (!progressEvent.lengthComputable) {
                updateProgress(18, "Uploading training batch...");
                return;
            }
            const rawPercent = (progressEvent.loaded / progressEvent.total) * 100;
            updateProgress(Math.min(92, rawPercent), "Uploading training batch...");
        });

        xhr.upload.addEventListener("load", function () {
            updateProgress(94, "Processing rows and building preview...");
        });

        xhr.onload = function () {
            const data = xhr.response || {};
            const ok = xhr.status >= 200 && xhr.status < 300 && data.ok !== false;
            updateProgress(100, ok ? "Upload complete." : "Upload finished with errors.");
            renderFeedback(data, !ok);

            if (ok) {
                uploadForm.reset();
            }

            if (submitButton) {
                submitButton.disabled = false;
                submitButton.textContent = defaultButtonLabel;
            }
        };

        xhr.onerror = function () {
            updateProgress(100, "Upload failed.");
            renderFeedback(
                {
                    message: "The upload could not be completed. Please try again.",
                    status_label: "Failed",
                },
                true
            );
            if (submitButton) {
                submitButton.disabled = false;
                submitButton.textContent = defaultButtonLabel;
            }
        };

        xhr.send(formData);
    });
})();
