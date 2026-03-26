let recorder;
let recorderStream;
let cameraStream;
let audioChunks = [];
let timerInterval;
let seconds = 0;

function previewImage(event) {
    const preview = document.getElementById("preview");
    const file = event.target.files[0];

    if (!preview) {
        return;
    }

    if (!file) {
        preview.removeAttribute("src");
        preview.classList.remove("is-visible");
        return;
    }

    const reader = new FileReader();
    reader.onload = function () {
        preview.src = reader.result;
        preview.classList.add("is-visible");
    };
    reader.readAsDataURL(file);
}

async function openCamera() {
    const video = document.getElementById("camera");
    if (!video) {
        return;
    }

    try {
        if (cameraStream) {
            cameraStream.getTracks().forEach((track) => track.stop());
        }

        cameraStream = await navigator.mediaDevices.getUserMedia({ video: true });
        video.srcObject = cameraStream;
        video.style.display = "block";
    } catch (error) {
        alert("Camera access is not available right now.");
    }
}

function capturePhoto() {
    const video = document.getElementById("camera");
    const preview = document.getElementById("preview");
    const imageInput = document.querySelector('input[name="image"]');

    if (!video || !video.srcObject) {
        alert("Open the camera before capturing a photo.");
        return;
    }

    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext("2d").drawImage(video, 0, 0);

    const dataURL = canvas.toDataURL("image/jpeg");
    if (preview) {
        preview.src = dataURL;
        preview.classList.add("is-visible");
    }

    fetch(dataURL)
        .then((response) => response.blob())
        .then((blob) => {
            const imageFile = new File([blob], "camera-capture.jpg", { type: "image/jpeg" });
            const dataTransfer = new DataTransfer();
            dataTransfer.items.add(imageFile);

            if (imageInput) {
                imageInput.files = dataTransfer.files;
            }
        });

    if (cameraStream) {
        cameraStream.getTracks().forEach((track) => track.stop());
        cameraStream = null;
    }

    video.srcObject = null;
    video.style.display = "none";
}

async function startRec() {
    try {
        recorderStream = await navigator.mediaDevices.getUserMedia({ audio: true });
        recorder = new MediaRecorder(recorderStream);
        recorder.start();

        audioChunks = [];
        seconds = 0;

        const timer = document.getElementById("timer");
        if (timer) {
            timer.textContent = "00:00";
        }

        timerInterval = window.setInterval(() => {
            seconds += 1;
            const minutes = Math.floor(seconds / 60);
            const remainingSeconds = seconds % 60;

            if (timer) {
                timer.textContent = `${minutes.toString().padStart(2, "0")}:${remainingSeconds
                    .toString()
                    .padStart(2, "0")}`;
            }
        }, 1000);

        recorder.ondataavailable = (event) => audioChunks.push(event.data);
    } catch (error) {
        alert("Microphone access is not available right now.");
    }
}

function stopRec() {
    if (!recorder || recorder.state === "inactive") {
        alert("Recording has not started.");
        return;
    }

    recorder.stop();
    window.clearInterval(timerInterval);

    recorder.onstop = () => {
        const blob = new Blob(audioChunks, { type: recorder.mimeType || "audio/webm" });
        const extension = blob.type.includes("mp4") ? "mp4" : "webm";
        const audioFile = new File([blob], `recording.${extension}`, { type: blob.type });
        const dataTransfer = new DataTransfer();
        dataTransfer.items.add(audioFile);

        const audioInput = document.getElementById("audioFile");
        if (audioInput) {
            audioInput.files = dataTransfer.files;
        }

        if (recorderStream) {
            recorderStream.getTracks().forEach((track) => track.stop());
            recorderStream = null;
        }
    };
}

function wireMainFormLoadingState() {
    const mainForm = document.getElementById("mainForm");
    if (!mainForm) {
        return;
    }

    mainForm.addEventListener("submit", () => {
        const button = document.getElementById("analyzeBtn");
        if (button) {
            button.innerHTML = "<span class='loader'></span>Analyzing...";
            button.disabled = true;
        }
    });
}

document.addEventListener("DOMContentLoaded", function () {
    wireMainFormLoadingState();
});

window.previewImage = previewImage;
window.openCamera = openCamera;
window.capturePhoto = capturePhoto;
window.startRec = startRec;
window.stopRec = stopRec;
