# Tines story templates

Tines stories are usually **exported/imported as JSON** from your own tenant ([Tines: importing and exporting stories](https://www.tines.com/docs/stories/importing-and-exporting)). Export files contain **team IDs, action IDs, and credential references** that are not portable across organizations, so this repo ships a **build blueprint** instead of a drop-in import.

**Start here:** [docs/TINES_STORY_TEMPLATE.md](../../docs/TINES_STORY_TEMPLATE.md) — step-by-step story layout, credentials, HTTP actions for `_reindex` / `_tasks`, and branching on outcomes.

After you build the story in Tines, **export** it from the UI and store the JSON in **your** internal repo or secret manager if you want version control—**redact** URLs and rotate any leaked keys.

For handoffs to customers or your team, a **short screen recording** or **PDF/screenshots** of the finished story (trigger → HTTP actions → branches)—stored in your wiki or LMS, not in this repo—often shortens onboarding more than text alone.
