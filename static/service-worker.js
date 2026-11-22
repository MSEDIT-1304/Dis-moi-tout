
const CACHE_NAME = "dis-moi-tout-v2";

self.addEventListener("install", (event) => {
    self.skipWaiting();
});

self.addEventListener("activate", (event) => {
    event.waitUntil(self.clients.claim());
});

// Les requêtes restent gérées par le serveur.
// Aucune page privée ou aucun agenda n'est mis en cache.
self.addEventListener("fetch", (event) => {
    if (event.request.method === "GET") {
        event.respondWith(fetch(event.request));
    }
});

// Réception des notifications Push, même lorsque
// l'application n'est pas ouverte.
self.addEventListener("push", (event) => {
    let data = {};

    if (event.data) {
        try {
            data = event.data.json();
        } catch (error) {
            data = {
                body: event.data.text()
            };
        }
    }

    const title = data.title || "Dis-moi tout";
    const unreadCount = Number(data.badgeCount || data.count || 0);

    const options = {
        body: data.body || "Vous avez reçu un nouveau message.",
        icon: "/static/images/app-icon-192.png",
        badge: "/static/images/favicon-32.png",
        data: {
            url: data.url || "/messagerie"
        },
        tag: "dis-moi-tout-message",
        renotify: true
    };

    // Mise à jour du compteur sur l'icône.
    const updateAppBadge = async () => {
        try {
            if (self.navigator && "setAppBadge" in self.navigator) {
                if (unreadCount > 0) {
                    await self.navigator.setAppBadge(unreadCount);
                } else if ("clearAppBadge" in self.navigator) {
                    await self.navigator.clearAppBadge();
                }
            }
        } catch (error) {
            console.warn("Mise à jour du badge indisponible :", error);
        }
    };

    event.waitUntil(Promise.all([
        self.registration.showNotification(title, options),
        updateAppBadge()
    ]));
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
