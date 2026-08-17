# kz.lixel.io

A story-driven photo album from a week in Kazakhstan, August 2026.

Static site built with [Astro](https://astro.build), photos processed by a Python
pipeline, deployed to GitHub Pages at **https://kz.lixel.io**.

## Layout

```
photos/originals/     full-res JPEGs (gitignored — keep your own backup)
tools/                Python image pipeline
public/photos/        generated web derivatives (committed)
src/                  Astro site — content, components, pages
```

## Getting started

```powershell
npm install
python tools/build_photos.py
npm run dev              # http://localhost:4321
```

## Publishing

Push to `main`. A GitHub Actions workflow builds the site and deploys it to GitHub Pages.

The site is unlisted (`noindex`, not linked from `lixel.io`) but publicly reachable.
See `CLAUDE.md` for the full set of decisions and constraints.
