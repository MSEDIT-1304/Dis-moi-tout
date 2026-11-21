
const CACHE_NAME = "dis-moi-tout-v1";

self.addEventListener("install", (event) => {
    self.skipWaiting();
});

self.addEventListener("activate", (event) => {
    event.waitUntil(self.clients.claim());
});

// Les requêtes réseau restent gérées par le serveur,
// sans mise en cache des pages privées ou des agendas.
self.addEventListener("fetch", (event) => {
    if (event.request.method === "GET") {
        event.respondWith(fetch(event.request));
    }
});

// Réception des notifications envoyées par le serveur.
self.addEventListener("push", (event) => {
    let data = {};

    if (event.data) {
        try {
            data = event.data.json();
        } catch (error) {
            data = { body: event.data.text() };
        }
    }

    const title = data.title || "Dis-moi tout";

    const options = {
        body: data.body || "Vous avez reçu un nouveau message.",
        icon: "/static/images/app-icon-192.png",
        badge: "/static/images/favicon-32.png",
        data: {
            url: data.url || "/messagerie"
        }
    };

    event.waitUntil((async () => {
        // Mise à jour du badge sur l'icône de l'application
        // lorsque le navigateur prend en charge cette fonction.
        const count = Number(data.badgeCount);

        if (
            self.navigator &&
            "setAppBadge" in self.navigator &&
            Number.isFinite(count)
        ) {
            if (count > 0) {
                await self.navigator.setAppBadge(count);
            } else if ("clearAppBadge" in self.navigator) {
                await self.navigator.clearAppBadge();
            }
        }

        // Affichage de la notification
        await self.registration.showNotification(title, options);
    })());
});

// Clic sur la notification : ouverture de la messagerie.
self.addEventListener("notificationclick", (event) => {
    event.notification.close();

    const targetUrl = new URL(
        event.notification.data && event.notification.data.url
            ? event.notification.data.url
            : "/messagerie",
        self.location.origin
    ).href;

    event.waitUntil((async () => {
        const windows = await self.clients.matchAll({
            type: "window",
            includeUncontrolled: true
        });

        for (const client of windows) {
            if (
                client.url.startsWith(self.location.origin) &&
                "focus" in client
            ) {
                await client.navigate(targetUrl);
                return client.focus();
            }
        }

        return self.clients.openWindow(targetUrl);
    })());
});
