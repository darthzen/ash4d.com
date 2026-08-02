// Minimal entrypoint. Exists for one reason: `html_handling: "none"` keeps
// `/fossa-mcp.html` serving at its own URL (matching the origin, so no
// published URL changes at cutover), but it also stops `/` resolving to
// index.html. This maps the root back, and passes everything else through
// untouched.
//
// If html_handling ever returns to a trailing-slash mode, delete this file and
// the `main` line in wrangler.jsonc — the Worker becomes assets-only again.

export default {
	async fetch(request, env) {
		const url = new URL(request.url);

		if (url.pathname === "/") {
			url.pathname = "/index.html";
			return env.ASSETS.fetch(new Request(url, request));
		}

		return env.ASSETS.fetch(request);
	},
};
