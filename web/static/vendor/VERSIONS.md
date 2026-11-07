# Vendored front-end files

Served from here, never from a CDN: KeaNexus must keep working when the WAN is
down, which is exactly when you are debugging DHCP. There is no Node build
step; these are the published minified builds, copied as-is.

| File | Package | Source in package | SHA-256 |
| --- | --- | --- | --- |
| `htmx-2.0.11.min.js` | `htmx.org@2.0.11` (npm) | `dist/htmx.min.js` | `d6fdc75f204e6bdefa99b69bf1e6d4ac69b8a364f77929f45c13476b4000f717` |
| `alpinejs-3.17.5.min.js` | `alpinejs@3.17.5` (npm) | `dist/cdn.min.js` | `b4036e9e9ac22fa6f6ae1ba292e015e1f3e9c85d0d79e73e4c8eb9bb139b522e` |

To upgrade: `npm pack <package>@<version>`, copy the file named above under a
new versioned filename, update `web/templates/base.html` and this table, and
check the hash with `shasum -a 256 <file>`.
