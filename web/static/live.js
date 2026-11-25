/*
 * live.js — Keeps the live panels honest: wakes them when a tab comes back,
 * and lets a panel's poll wait while an action is still writing into it.
 *
 * Loaded on every page by base.html, after htmx. Plain browser script, no
 * module: the pages use classic <script defer> tags.
 */
(function () {
	"use strict";

	/*
	 * Waking. Every live panel polls with hx-trigger="every Ns", but phones
	 * (iOS Safari especially) freeze timers in background tabs and restore
	 * pages from the back/forward cache, so a returning tab shows whatever it
	 * held when it went to sleep. On return we fire one "page-visible" event on
	 * <body>; each panel's hx-trigger lists "page-visible from:body".
	 */
	function wake() {
		if (window.htmx) {
			window.htmx.trigger(document.body, "page-visible");
		}
	}

	document.addEventListener("visibilitychange", function () {
		if (document.visibilityState === "visible") {
			wake();
		}
	});

	// Restored from the back/forward cache: no load event fires, so nothing
	// else would refresh. persisted is false on a normal load, which is fresh.
	window.addEventListener("pageshow", function (event) {
		if (event.persisted) {
			wake();
		}
	});

	/*
	 * Waiting. KeaNexus.busy("leases-table") is true while any request aimed
	 * at #leases-table is in flight (htmx marks the issuing element with
	 * .htmx-request). A sweep or a bulk removal can run for seconds; if the
	 * panel's own poll swapped it out meanwhile, the action's answer would
	 * land in a detached element and never be drawn. So polls use it as a
	 * filter: hx-trigger="every 60s[!KeaNexus.busy('leases-table')]".
	 */
	window.KeaNexus = {
		busy: function (panelId) {
			return document.querySelector('.htmx-request[hx-target="#' + panelId + '"]') !== null;
		},
	};
})();
