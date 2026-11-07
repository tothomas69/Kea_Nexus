# Vendored front-end files

Served from here, never from a CDN: KeaNexus must keep working when the WAN is
down, which is exactly when you are debugging DHCP. There is no Node build
step; these are the published minified builds, copied as-is.

| File | Package | Source in package | SHA-256 |
| --- | --- | --- | --- |
| `htmx-2.0.11.min.js` | `htmx.org@2.0.11` (npm) | `dist/htmx.min.js` | `d6fdc75f204e6bdefa99b69bf1e6d4ac69b8a364f77929f45c13476b4000f717` |
| `alpinejs-3.17.5.min.js` | `alpinejs@3.17.5` (npm) | `dist/cdn.min.js` | `b4036e9e9ac22fa6f6ae1ba292e015e1f3e9c85d0d79e73e4c8eb9bb139b522e` |
| `vega-6.4.0.min.js` | `vega@6.4.0` (npm) | `build/vega.min.js` | `8f6a3587cf8d4f42c7e08120e3eb05d067e746d554e39d2dcf52acc0bd5ba28f` |
| `vega-lite-6.4.1.min.js` | `vega-lite@6.4.1` (npm) | `build/vega-lite.min.js` | `6d5035fdd429b4bc6f91f3754426c3f516f3dbd8e08b105cfc2496bed4ebd254` |
| `vega-embed-7.3.0.min.js` | `vega-embed@7.3.0` (npm) | `build/vega-embed.min.js`, trailing `sourceMappingURL` comment removed | `943452d808cdd9d985076d24de54c8b3911be70c385ffc14c5a5225e7b25b368` |

To upgrade: `npm pack <package>@<version>`, copy the file named above under a
new versioned filename, update `web/templates/base.html` and this table, and
check the hash with `shasum -a 256 <file>`.

**Vega-Lite is pinned to Altair's schema version** (Altair 6.3 emits Vega-Lite
6.4.1 specs; see `alt.SCHEMA_VERSION`). Bump the two together. vega-embed's
source map is not vendored, so its `sourceMappingURL` line is stripped to avoid
a 404 in the browser console; that is why its hash differs from the package's.
