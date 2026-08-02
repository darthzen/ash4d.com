// Minimal entrypoint. Exists for one reason: `html_handling: "none"` keeps
// `/fossa-mcp.html` serving at its own URL (matching the origin, so no
// published URL changes at cutover), but it also stops `/` resolving to
// index.html. This maps the root back, and passes everything else through
// untouched.
//
// If html_handling ever returns to a trailing-slash mode, delete this file and
// the `main` line in wrangler.jsonc — the Worker becomes assets-only again.

// Token path -> asset path. One entry per recipient; the token is random
// (secrets.token_urlsafe) so the URL is unguessable and a hit is attributable.
// Anything not listed here falls through to the normal 404, so a wrong token
// never serves the page.
const TRACKING_PATHS = new Map([["/for/AlczhpBFB26dzeRX", "/fossa-mcp.html"]]);

export default {
	async fetch(request, env) {
		const url = new URL(request.url);

		if (url.pathname === "/") {
			url.pathname = "/index.html";
			return env.ASSETS.fetch(new Request(url, request));
		}

		// Per-recipient tracking path. Each token under /for/ is handed to exactly
		// one person, so a hit on that path in Cloudflare analytics identifies who
		// opened the page. Zone analytics groups by clientRequestPath and throws
		// the query string away, which is why the token has to live in the path
		// rather than in a ?ref= parameter.
		//
		// This must stay an internal rewrite. A 301/302 to /fossa-mcp.html would
		// record the analytics hit against the shared path and lose the
		// attribution entirely — the redirect target is what gets counted. Serve
		// the asset's bytes under the token URL instead.
		if (TRACKING_PATHS.has(url.pathname)) {
			url.pathname = TRACKING_PATHS.get(url.pathname);
			return env.ASSETS.fetch(new Request(url, request));
		}

		return env.ASSETS.fetch(request);
	},
};
