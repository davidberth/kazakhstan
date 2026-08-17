import { defineCollection, z } from 'astro:content';
import { glob } from 'astro/loaders';

// Chapters are Markdown so the prose is pleasant to edit. Frontmatter carries
// the metadata; the body is the story text.
//
// `group` is the join key back to the photo manifest — it must match the
// folder name under photos/originals/ with any numeric prefix stripped.
const chapters = defineCollection({
  loader: glob({ pattern: '**/*.md', base: './src/content/chapters' }),
  schema: z.object({
    title: z.string(),
    order: z.number(),
    group: z.string(),
    place: z.string().optional(),
    lead: z.string().optional(),
  }),
});

export const collections = { chapters };
