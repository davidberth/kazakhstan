import { defineCollection, z } from 'astro:content';
import { glob } from 'astro/loaders';

// Chapters are Markdown so the prose is pleasant to edit. Frontmatter carries
// the metadata; the body is the story text.
//
// `group` is the join key back to the photo manifest. It must match the
// folder name under photos/originals/ with any numeric prefix stripped.
const chapters = defineCollection({
  loader: glob({ pattern: '**/*.md', base: './src/content/chapters' }),
  schema: z.object({
    title: z.string(),
    order: z.number(),
    group: z.string(),
    place: z.string().optional(),
    lead: z.string().optional(),

    // Kazakh alongside the English, so the album can be shared with family who
    // read Kazakh. Optional: a chapter without a translation simply renders
    // English only rather than breaking the build.
    title_kk: z.string().optional(),
    place_kk: z.string().optional(),
    lead_kk: z.string().optional(),
    body_kk: z.string().optional(),
  }),
});

export const collections = { chapters };
