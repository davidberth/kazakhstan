// @ts-check
import { defineConfig } from 'astro/config';

export default defineConfig({
  // Used to build absolute URLs (canonical tags, sitemap). Must match the live host.
  site: 'https://kz.lixel.io',

  // Emit clean URLs: /chapters/almaty/ rather than /chapters/almaty.html
  build: {
    format: 'directory',
  },
});
