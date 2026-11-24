// eslint.config.js - ESLint flat config (v9+)
export default [
	{
		// Third-party bundles, minified: not ours to lint.
		ignores: ["web/static/vendor/**"],
	},
	{
		files: ["web/static/**/*.js"],
		languageOptions: {
			// The app's JS runs as classic <script defer> tags in the browser,
			// not as ES modules, so declare what the page provides.
			sourceType: "script",
			globals: {
				// Browser built-ins the scripts use.
				window: "readonly",
				document: "readonly",
				console: "readonly",
				// Vendored libraries loaded by base.html / overview.html
				// ahead of our scripts.
				Alpine: "readonly",
				htmx: "readonly",
				vegaEmbed: "readonly",
			},
		},
	},
	{
		rules: {
			"no-unused-vars": "error",
			"no-undef": "error",
			// console.error/warn report real failures (a chart that couldn't
			// draw); plain console.log is still flagged as leftover debugging.
			"no-console": ["warn", { allow: ["warn", "error"] }],
			eqeqeq: "error",
			"prefer-const": "error",
			"no-var": "error",
		},
	},
];
