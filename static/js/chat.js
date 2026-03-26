function wireChatFormLoadingState() {
    const chatForm = document.querySelector(".chat-form");
    if (!chatForm) {
        return;
    }

    chatForm.addEventListener("submit", () => {
        const submitButton = chatForm.querySelector('button[type="submit"]');
        if (submitButton) {
            submitButton.innerHTML = "<span class='loader'></span>Sending...";
            submitButton.disabled = true;
        }

        const chatBox = document.getElementById("chatBox");
        if (chatBox) {
            const loader = document.createElement("div");
            loader.className = "message assistant";
            loader.innerHTML =
                "<div class='meta'>Assistant</div><div class='text'>Preparing a response...</div>";
            chatBox.appendChild(loader);
            chatBox.scrollTop = chatBox.scrollHeight;
        }
    });
}

document.addEventListener("DOMContentLoaded", function () {
    const chatBox = document.getElementById("chatBox");
    if (chatBox) {
        chatBox.scrollTop = chatBox.scrollHeight;
    }

    wireChatFormLoadingState();
});
