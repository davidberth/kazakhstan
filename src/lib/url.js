/**
 * Join a repo-relative asset path onto the site's base path.
 *
 * The site is served from a subpath (/kazakhstan), so every URL needs that
 * prefix. `import.meta.env.BASE_URL` may or may not carry a trailing slash
 * depending on Astro version and config, which makes naive template
 * concatenation produce `/kazakhstanphotos/...`. Normalize both sides once,
 * here, and never build an asset URL by hand anywhere else.
 *
 *   asset('photos/x.webp')  ->  /kazakhstan/photos/x.webp
 *   asset('/maps/y.png')    ->  /kazakhstan/maps/y.png
 */
export function asset(path) {
  const base = import.meta.env.BASE_URL.replace(/\/+$/, '');
  return `${base}/${String(path).replace(/^\/+/, '')}`;
}
