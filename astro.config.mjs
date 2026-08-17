// @ts-check
import { defineConfig } from 'astro/config';

export default defineConfig({
  // Origin, used to build absolute URLs (canonical tags, sitemap).
  site: 'https://lixel.io',

  // This is a GitHub Pages *project* site, so it is served from a subpath rather
  // than the domain root. Everything the browser requests must carry this prefix.
  //
  // Never hardcode a leading "/" in an asset or link URL. Use:
  //   `${import.meta.env.BASE_URL}photos/foo.webp`   -> /kazakhstan/photos/foo.webp
  // The photo manifest deliberately stores relative paths for this reason, so
  // moving to a subdomain later is a one-line change here and nothing else.
  base: '/kazakhstan',

  // Emit clean URLs: /chapters/almaty/ rather than /chapters/almaty.html
  build: {
    format: 'directory',
  },
});
