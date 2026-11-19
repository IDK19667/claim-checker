# Evident UI audit (external, Nov 2026)
Source: an outside design review Dhruva pasted in. It's data to check, not orders. Verify each finding yourself before acting on it.

## Measured problems (confirm these first)
- Claim field starts about 8,532px down on desktop, 8,782px on tablet, about 825px on an 844px phone screen (the submit button is below the first screen), and still about 3,051px down with reduced motion on.
- Mobile result: the lab image and oversized verdict push the explanation below the sticky share bar.
- Validation message "Type or paste the claim first." stays after valid text is entered.
- Near-duplicate suggestions show separately ("creatine builds strength" / "Creatine improves strength").
- Old saved result: "Rests on 5 studies" with "3 used for this verdict", and an empty search query. Fresh checks were consistent, so this is a saved-record validation bug.
- Source dialogs only have a Close button at the bottom (Escape works).
- Repeated effect-size sentence in one inspected result.
- Study-type text "About as strong as evidence gets" overstates it, because design alone doesn't set certainty.
- Desktop: an expanded breakdown leaves a long narrow column next to a mostly empty source column.
- Dark input field blends into the dark background, and the placeholder is the only visible label.
- Important labels at 11px, explanations at 13px.

## Works, keep it
Arrow-key suggestions, Escape to dismiss, focus returns after dialogs, "Link copied" confirmation, report cancel, return to claim field, the color palette (contrast passed), restrained corners and icons, truthful loading stages, Cancel.

## Proposed direction
- First screen: brand, nav (How it works · About · Privacy), heading, labelled claim field ("Health claim to check" / "Enter one claim. Add the group or situation if it matters."), submit, and 3 example buttons. The cinematic intro moves below or goes.
- Privacy note beside submit, matching the real behavior. Draft: "Don't include names or personal medical details. Claim text is stored and sent to our AI provider."
- Counters move below the form or to About.
- Suggestions: group near-duplicates, label "Previously checked claims", make it clear whether a pick opens a saved answer or fills the field.
- Loading: drop the big banner and keep the claim in a compact, stable layout. Separate error types: invalid input, network, rate limit, provider failure, no relevant evidence. Keep the draft.
- Result order:
  1. the claim, with an edit option
  2. a one-sentence qualified answer
  3. assessment and confidence
  4. who and what outcome it applies to
  5. the main limitation
  6. sources, each with why it matters
  7. an optional breakdown or book visual
  8. search details and dates
  9. share
  10. report an issue
- The result page has no lab image and no big oval verdict label. Use one main reading column and a narrow evidence rail.
- Share comes after the answer. The mobile sticky share appears only after the summary.
- Sticky Close at the top of dialogs.
- Footer: About · Methodology · Corrections/Contact · Privacy, plus one medical-scope line. The FAQ loses the repeated "Check a claim" links.
- Type sizes: headings 48–56px on desktop and 34–38px on mobile, slightly looser tracking. Body 16–17px with about 1.6 line height. Helper text 14–15px, metadata 13–14px. Keep Bricolage for headings and Instrument Sans for body text.
- Colors to keep:

  | Role | Hex |
  |---|---|
  | Background | #F1F2EE |
  | Surface | #FAFAF8 |
  | Text | #121514 |
  | Secondary text | #434A46 |
  | Muted text | #5D6560 |
  | Action | #2C5848 |
  | Highlight | #D4E4DA |
  | Dark surface | #0F1211 |

  Green must never imply "safe".
- Touch targets 44–48px (WCAG AA minimum is 24px).

## Not tested by the audit
Screen readers, text zoom, soft keyboards, native share, speech permissions, outages, clinical accuracy, real-user conversion.
