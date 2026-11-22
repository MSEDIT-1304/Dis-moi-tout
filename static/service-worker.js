
const CACHE_NAME = "dis-moi-tout-v3";

self.addEventListener("install", (event) => {
    self.skipWaiting();
});

self.addEventListener("activate", (event) => {
    event.waitUntil(self.clients.claim());
});

// Les pages et données privées ne sont jamais mises en cache.
self.addEventListener("fetch", (event) => {
    if (event.request.method === "GET") {
        event.respondWith(fetch(event.request));
    }
});

// Réception d'un message même lorsque l'application est fermée.
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
        tag: "dis-moi-tout-message",
        renotify: true,
        data: {
            url: data.url || "/messagerie"
        }
    };

    event.waitUntil((async () => {
        // Affichage prioritaire de la notification.
        await self.registration.showNotification(title, options);

        // Mise à jour du compteur si le système le permet.
        const count = Number(data.badgeCount || data.count || 0);

        try {
            if (count > 0 && "setAppBadge" in self.navigator) {
                await self.navigator.setAppBadge(count);
            } else if (count <= 0 && "clearAppBadge" in self.navigator) {
                await self.navigator.clearAppBadge();
            }
        } catch (error) {
            console.warn("Compteur d'icône non pris en charge :", error);
        }
    })());
});

// Ouverture de la conversation au clic sur la notification.
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
