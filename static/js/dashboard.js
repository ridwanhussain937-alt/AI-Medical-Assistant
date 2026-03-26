document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll(".bar-column").forEach((column) => {
        const label = column.querySelector(".bar-label");
        const value = column.querySelector(".bar-value");
        if (label && value) {
            column.setAttribute("title", `${label.textContent}: ${value.textContent}`);
        }
    });
});
