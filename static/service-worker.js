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
        // Mise à jour du badge de l'application installée.
        const rawCount = data.badgeCount ?? data.count;
        const count = Number(rawCount);

        try {
            if (
                Number.isFinite(count) &&
                count > 0 &&
                typeof navigator.setAppBadge === "function"
            ) {
                await navigator.setAppBadge(Math.floor(count));
            } else if (
                (!Number.isFinite(count) || count <= 0) &&
                typeof navigator.clearAppBadge === "function"
            ) {
                await navigator.clearAppBadge();
            }
        } catch (error) {
            // Une erreur de badge ne doit pas bloquer la notification.
            console.warn(
                "Impossible de mettre à jour le badge de l'application :",
                error
            );
        }

        // La notification reste affichée même si le badge n'est pas pris en charge.
        await self.registration.showNotification(title, options);
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
