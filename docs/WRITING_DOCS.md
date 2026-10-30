# Writing docs

How the docs in this repo are written. The general rules match the `docs-writing` agent skill,
copied here so they apply without it.

## This repo

- One canonical home per fact. The map of where each topic lives is the [Docs](../README.md#docs) section of the README.
- Field-level API detail goes in `Field(description=...)` and route docstrings, which become ReDoc. A `#` comment never reaches `openapi.json`.
- Code comments that cite a doc use its path and section heading, so renaming a heading means searching the repo for it.

## General

Docs should read like an experienced engineer wrote them for a colleague: plain, specific, and
easy to scan. Density usually comes from how information is presented, not from length alone.
Most of these rules are about structure and word choice.

### Structure

- Open each doc with one or two sentences on what the thing is or does. No preamble about what
  the doc will cover.
- Put the concrete thing first: the request, the config line, the code, the command. Explain
  around it.
- One topic per section. If a section answers two questions, split it.
- Separate reference from walkthroughs. Reference is tables and short definitions, ordered for
  lookup. Walkthroughs are numbered steps, ordered for doing, and end with a check that it worked.
- Order sections by how often a reader needs them, not by how the code is organized.
- Use tables only for facts that enumerate (fields, status codes, env vars). Use bullets for
  short parallel items. Everything else is paragraphs.
- Keep paragraphs to two to four sentences.
- Headings name the thing: `input_sources`, `Baseline merge`, `Errors`. No questions, no task
  phrasing ("Where each value came from"), no headings that are full sentences.
- Each fact lives in one doc. Elsewhere, link to it.
- If a generated reference exists (OpenAPI, ReDoc, typedoc), don't restate it field by field.
  Write what the schema can't say and link to the generated page.

### Sentences

- No second person. No "you" or "your". Describe what the system does ("The service reads the
  current value with `model.get()`"). Numbered steps in a walkthrough may use the imperative
  ("Set `LUME_MODELS`").
- Use concrete nouns and specific values: `Retry-After: 2`, `±50% of the default`, `logged at
  INFO`. Avoid "a couple of", "various", "certain".
- Use plain verbs: reads, sets, skips, returns, fails. Avoid "leverages", "facilitates",
  "ensures", "is responsible for".
- Fold the reason into the sentence that needs it, usually with "so", "because", or by stating
  the consequence: "uses a fixed stride instead of a random sample, so two workers return the
  same particles." Never use labelled rationale blocks ("Why:", "Rationale:", "Note:").
- State a rationale once, briefly, where it changes how someone reads or changes the code. Long
  history and incident write-ups go in a design-decisions doc, not inline.
- No em dashes and no semicolons. Use a period, a comma, or parentheses instead. No emojis.
- Contractions are fine. Parentheticals are fine when they hold a concrete example or a short
  definition.
- Define an unfamiliar term the first time it appears, in a few words.

### Things to cut

These are what make docs sound machine-written:

- Editorial asides that grade or advise instead of informing: "Worth highlighting", "That's a
  stopgap", "This is important", "Keep in mind", "Note that", "It's worth noting".
- Wrap-up sentences that restate the paragraph, and closing summaries of the section.
- Pre-empting objections: "This is not X, it is Y", "rather than...", "deliberately",
  "on purpose", "load-bearing", "in the way that matters".
- Intensifiers and filler: "exactly", "simply", "just", "actually", "the whole story",
  "entirely", "genuinely", "crucially".
- Chains of clauses joined with ", so", ", which means", ", because" more than once per
  sentence. Split them.
- Every edge case the code handles. Document behavior a reader can observe or has to account
  for. Leave the rest to code comments.
- Marketing tone, praise of the design, and hedging ("should generally", "typically tends to").

### Accuracy

- Check every claim against the code before writing it. When the doc and the code disagree,
  the code wins. Record the disagreement for the maintainer, and don't silently change code to
  match a doc.
- Mark anything that couldn't be verified.
- Refer to code by file path plus function or symbol name, never by line number.
- When renaming a doc or a heading, search the repo for references to it (code comments, tests,
  CI, other docs) and update them in the same change.

### Example

Before:

> This is what makes the service stateless in the way that matters. A model instance is
> stateful by nature: `set()` mutates it. If a request only applied the knobs it named, the
> result would depend on whatever the previous request left behind on that particular worker,
> so two identical requests could get different answers depending on which of K workers picked
> them up, and a replica restart would change results. Merging over the baseline removes that
> entirely.

After:

> Every evaluate sets all non-constant inputs, not just the ones in the request. The request is
> merged over the design values and applied in one `model.set()`.
>
> A model instance keeps whatever values were last set on it, and each model runs on several
> workers. Applying only the requested inputs would make the result depend on what the previous
> request left on that worker. With the merge, the same request gives the same result on any
> worker.
