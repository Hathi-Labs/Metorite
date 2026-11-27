# v6 value first: research notes

Time spent: about 6 minutes. Three patterns, and what each changed.

1. **Hatch: a profile from a website URL.** The owner types a URL, and Hatch
   fills a business profile in seconds. It marks each AI-made field, and each
   field stays editable. Works: the owner corrects, and does not type from zero.
   Fails: a silent guess reads as a fact.
   https://www.usehatchapp.com/blog/auto-business-profile
2. **OfferZen: auto-made company profiles.** It asks the company to check the
   data, because enrichment is often wrong.
   https://docs.offerzen.com/en/articles/7986852-how-offerzen-uses-ai-to-auto-complete-company-profiles
3. **Soft gates in product-led tools.** A locked feature stays visible, and a
   click on it opens a prompt in context. Works better than a generic
   "upgrade" or "connect" button.
   https://skills.sh/skenetechnologies/plg-skills/feature-gating
4. **Clay and "value before the ask".** Signup answers become a concrete start
   before the user asks for one.
   https://supademo.com/content/plg-onboarding-gallery/clay

## Decisions that the research changed

- **Every draft field names its source** (website, map listing, GST register),
  and a guess says "estimate". When the owner corrects a field, its source
  changes to "You said". From Hatch and OfferZen. Before the research, the
  draft was one block of text with one "Draft" badge.
- **Each locked line is a button.** A click opens the consent sheet for that
  source, in place, at any time. The chat then skips that step. From the soft
  gate pattern. Before the research, the only way to connect was a chat chip.
- **The first question is "What did I get wrong?"**, not "Tell me about your
  business". The owner corrects a draft, which is faster than a blank answer.
