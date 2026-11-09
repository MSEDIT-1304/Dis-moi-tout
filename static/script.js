
document.addEventListener("DOMContentLoaded", function () {

    // Confirmation avant suppression
    document.querySelectorAll("[data-confirm]").forEach(function (element) {
        element.addEventListener("click", function (event) {
            const message = element.dataset.confirm || "Confirmer cette action ?";
            if (!confirm(message)) {
                event.preventDefault();
            }
        });
    });

    // Affichage et masquage des mots de passe
    document.querySelectorAll("[data-toggle-password]").forEach(function (button) {
        button.addEventListener("click", function () {
            const input = document.querySelector(button.dataset.togglePassword);

            if (input) {
                input.type = input.type === "password" ? "text" : "password";
            }
        });
    });

    // Fermeture des messages d'information
    document.querySelectorAll(".alert [data-close]").forEach(function (button) {
        button.addEventListener("click", function () {
            const alert = button.closest(".alert");
            if (alert) {
                alert.remove();
            }
        });
    });

});

