# Generation Targeting

This is the current seed reference the AI uses to choose `target_generation`.

It does not mean the explainer only flags these exact words. The OpenAI prompt can also flag short expressions that are confusing in context, including colloquial, internet-native, regional, Spanglish, or technical language. But these dictionary terms are the clearest baseline for generation assignment.

## Boomer

These terms currently map to `boomer`:

- `chévere`
- `cuate`
- `chamaco`
- `bacán`
- `gacho`

Typical intent:

- older or older-leaning colloquial Spanish
- familiar regional wording that can read dated or old-school to younger listeners

## Millennial

These terms currently map to `millennial`:

- `crush`
- `random`
- `fomo`
- `hater`
- `bro`
- `chamba`
- `neta`
- `chido`
- `lana`
- `bronca`

Typical intent:

- 2000s/2010s internet or casual urban slang
- everyday colloquial Mexican Spanish that still feels strongly millennial-coded in this app

## Gen Z

These terms currently map to `gen_z`:

- `cringe`
- `ghostear`
- `stalkear`
- `shippear`
- `tóxico`

Typical intent:

- social-media-native language
- Spanglish verbs and online relationship vocabulary

## How The AI Picks A Generation

The explainer in `openai_reasoner.py` is currently tuned like this:

- If the latest line contains a known dictionary term, that term is treated as already valid and eligible to flag.
- For known dictionary terms, the dictionary stays authoritative for the term, definition, and generation.
- In that known-term path, the model is only asked to explain why the term was used in the conversation.
- It assumes the conversation is usually intra-generational.
- It does not flag a word just because it sounds young.
- It tries to flag the one short term or expression that is actually confusing in that moment.
- It must still choose one of `boomer`, `millennial`, or `gen_z`.
- `target_generation` should be the audience that would most benefit from a quick explanation.

Practical rule of thumb:

- `boomer`: older-leaning colloquial Spanish or wording that feels dated/regional.
- `millennial`: mainstream casual slang, 2000s/2010s internet vocabulary, broad office/social usage.
- `gen_z`: newer online slang, parasocial/social-media language, Spanglish verbs, and newer tone-heavy expressions.

## Current Limitation

The dictionaries are intentionally small right now. If you want tighter generation tagging, the most reliable improvement is to keep expanding the seed lists in:

- `dictionary/boomer.json`
- `dictionary/millennial.json`
- `dictionary/gen_z.json`

That gives both the transcription hints and the explanation prompt better anchors.

## Curation Guidance

Terms that drift across generations are the easiest way to get weak tagging.

Good candidates for removal or reclassification are:

- words that are now mainstream enough to feel cross-generational
- broad regional Mexican Spanish that is not strongly tied to one age cohort
- internet words that started with millennials but are now more strongly gen-z-coded in present-day usage

Examples to review in the current seeds:

- `hater`: probably too modern to ever map to `boomer`; depending on product intent, it may fit `millennial` or `gen_z`, or be too mainstream to use as a strong anchor
- `bro`: broad enough that it may be a weak generation anchor
- `chido`, `neta`, `lana`, `bronca`: very common colloquial Mexican Spanish, useful as vocabulary but weaker as strict age markers
- `cuate`, `gacho`, `chévere`, `bacán`: can be regional or age-coded depending on speaker community, so they should be curated carefully

Practical rule:

- keep anchor lists opinionated and small
- prefer words that strongly signal an era
- remove words that nearly everyone already uses
