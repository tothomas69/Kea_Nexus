/*
 * overview.js — The Address Map's hover and pinned card (an Alpine component).
 *
 * The server renders every square and embeds all 256 cells' details as JSON
 * (#address-cells), so hovering never makes a request. When HTMX redraws the
 * map (every 30 s, or after a change), load() re-reads that JSON; a pinned
 * address stays pinned across the redraw.
 */

const CELLS_PER_ROW = 16;
// Past these, the card opens leftward / upward so it stays inside the map.
const FLIP_LEFT_FROM_COLUMN = 10;
const FLIP_UP_FROM_ROW = 9;
const CARD_GAP_PX = 6;

document.addEventListener("alpine:init", () => {
	Alpine.data("addressMap", (csrfToken, isAdmin) => ({
		cells: [],
		hovered: null,
		pinned: null,
		busy: false,
		isAdmin: isAdmin,

		init() {
			this.load();
		},

		load() {
			const source = document.getElementById("address-cells");
			this.cells = source ? JSON.parse(source.textContent) : [];
		},

		get focus() {
			const octet = this.pinned !== null ? this.pinned : this.hovered;
			return octet === null ? null : this.cells[octet] || null;
		},

		hover(octet) {
			if (this.pinned === null) this.hovered = octet;
		},

		leave() {
			this.hovered = null;
		},

		toggle(octet) {
			this.pinned = this.pinned === octet ? null : octet;
			this.hovered = null;
		},

		cardStyle() {
			const octet = this.pinned !== null ? this.pinned : this.hovered;
			const row = Math.floor(octet / CELLS_PER_ROW);
			const column = octet % CELLS_PER_ROW;
			const horizontal =
				column < FLIP_LEFT_FROM_COLUMN
					? `left: calc(${((column + 1) / CELLS_PER_ROW) * 100}% + ${CARD_GAP_PX}px);`
					: `right: calc(${((CELLS_PER_ROW - column) / CELLS_PER_ROW) * 100}% + ${CARD_GAP_PX}px);`;
			const vertical =
				row < FLIP_UP_FROM_ROW
					? `top: ${(row / CELLS_PER_ROW) * 100}%;`
					: `bottom: ${((CELLS_PER_ROW - row - 1) / CELLS_PER_ROW) * 100}%;`;
			return horizontal + vertical;
		},

		editStatic(cell) {
			htmx.ajax("GET", `/address/${cell.ip}/static`, { target: "#dialog-body", swap: "innerHTML" });
			this.$dispatch("open-dialog");
		},

		quarantineAction(cell) {
			const name = encodeURIComponent(cell.device);
			const url = cell.quarantined ? `/quarantine/${name}/release` : `/quarantine/${name}`;
			this.busy = true;
			htmx
				.ajax("POST", url, {
					target: "#quarantine-card",
					swap: "outerHTML",
					headers: { "X-CSRF-Token": csrfToken },
				})
				.finally(() => {
					this.busy = false;
				});
		},
	}));
});
