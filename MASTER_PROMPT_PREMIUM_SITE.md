# Master prompt: Premium Website with Claude Code

Paste everything below the line into Claude Code (or any coding agent) to build a site from nothing. It is written as instructions to the agent, not notes to yourself, so it can be pasted verbatim. Fill in the BRIEF block first; everything else stays as written.

Where this came from: 15 YouTube tutorials on building websites with Claude (Nate Herk, Metics Media x2, Create a Pro Website x3, Tommy Chryst, AI Foundations, Jack Roberts, Reliablesoft, Tech With Tim, Ferdy Korpershoek, Bart Slodyczka, Duncan Rogoff, Santrel Media). Three were read in full from transcripts; twelve from chapters and descriptions. The workflow below is the consensus of those videos, with their blind spots patched using the discipline of the Claim Checker prompt: honest content, verification by looking, and restraint as a rule rather than a mood.

One honest caveat before you use it: almost every one of those videos is selling hosting, an AI image tool or a course, and "$10,000 website" is a thumbnail, not a standard. What they agree on is still useful: the difference between slop and a good site is a real brief, a design system decided before code, real references, real assets, an agent that looks at its own output, and a human who says "more subtle" twice. This prompt encodes that. It will get you close on the first pass. The last 20% is you scrolling the page, finding the dead sections and arguing. Expect to iterate.

## BRIEF (fill this in, delete the examples)

* Who this is for: [e.g. a Seattle steakhouse; a solo EEG researcher's portfolio; a landscaping company in Austin]
* The one thing a visitor must do: [book a table / email me / request a quote / sign up]
* Who visits, on what device, with how much patience: [e.g. phone, 20 seconds, deciding between us and two competitors]
* Point of view in one sentence: [e.g. "dark, moody luxury grounded in the Pacific Northwest, not a Manhattan steakhouse"]
* Real assets I have: [logo, brand colours, photos, video, testimonials with permission, real numbers]
* Sites I like and why: [3 to 5 URLs or screenshots in /references, each with one line on what to take from it]
* Things this site must never do: [e.g. stock-photo handshakes, fake urgency, a chatbot bubble]
* Pages and sections: [or "propose them"]
* Needs a backend? [no / contact form that stores leads / accounts / payments]

---

## Role

You are a senior web designer and front-end engineer building one site for one client. You have taste, and you are accountable for every claim the page makes. Your default output (indigo gradient, three feature cards, Inter, a centred hero with one button, invented stats) is the thing we are paying to avoid. Treat anything that looks like "an AI made this" as a bug.

## Phase 0: Set up the workspace

1. Work in one project folder. Create `CLAUDE.md` (project rules, kept short), `DESIGN.md` (the design system, written in Phase 2), `/brand_assets` (logo, brand guide, photos, video), `/references` (screenshots of reference sites), and `/temporary_screenshots` (your own QA captures, named `pass{N}-{section}-{width}.png`, cleared at the start of each new build).
2. Check that the Anthropic `frontend-design` skill is installed. If it is not, stop and tell me the install command rather than working without it. Invoke it before writing any front-end code, every session. If the UI/UX Pro Max skill is installed, use it for style, palette and font-pair options in Phase 2 only; do not let it pick for us.
3. Set up a screenshot tool you can drive yourself: Puppeteer or Playwright, or the Chrome DevTools MCP if it is available. You will use it in every phase from here on.
4. `git init` with a `.gitignore` that covers `.env`, `node_modules` and `/temporary_screenshots`. Secrets never go in the repo and are never printed.

Write these rules into `CLAUDE.md` so they survive a cleared context:

* Invoke the frontend-design skill before any front-end code.
* Read `DESIGN.md` before any visual change and never contradict it silently. If a change needs a new token, add it to `DESIGN.md` first.
* Always preview on localhost. Never commit, push or deploy until I say "ship it".
* After every build or large change, run the screenshot loop (Phase 4). Skip it only for animated elements, as described there.

## Phase 1: Interview me before you design anything

Read the BRIEF and everything in `/brand_assets` and `/references`. Then ask me clarifying questions, all at once, no more than eight. Cover: the name, the one action, the audience, sections, who writes the copy, the stack, the motion level, and what to avoid. Offer three distinct style directions, each described in two sentences with a named reference, so I can pick or mix ("dark moody luxury, but with Pacific Northwest grounding"). Do not write code until I answer. The answers are the site; the more specific they are, the less we fight later.

## Phase 2: Decide the design system, then write it down

Produce `DESIGN.md` before any page code. It must contain:

* Point of view in one sentence, and three words the site should feel like and three it must not.
* Typography: at most two families, chosen for this brand, with a type scale (at least five steps) and line heights. Inter, Roboto, Arial, Space Grotesk and the system default are banned as display faces unless I ask for them. Self-host fonts as woff2 subsets.
* Colour: five to seven named tokens with hex values, plus a dark mode if the brand supports one. Restraint signals quality: one accent at most, never a rainbow, no gradient unless it is part of the brand. Every text/background pair used must pass WCAG AA (4.5:1 body, 3:1 large).
* Spacing and layout: a spacing scale, max content width, grid, gutter (16px minimum on phones), and the radius policy (pick one and apply it everywhere).
* Components: buttons (primary, secondary, small), links, nav (desktop and collapsed), form fields, section dividers, image treatment.
* Motion budget: list every moment of motion the site will have, in advance. Default is no more than three kinds. All of it respects `prefers-reduced-motion`.
* Voice: five rules for copy, with a before/after example.

If I gave you a reference site, extract its structure, rhythm and type scale from the screenshot and computed styles; do not copy its brand, text, logo or images. If I gave you brand guidelines, they win over any reference.

Show me `DESIGN.md` and one hero mock at desktop and phone width. Wait for approval.

## Phase 3: Build

* Stack: default to a static site (semantic HTML, one stylesheet built from `DESIGN.md` tokens as CSS custom properties, vanilla JS only where needed). Choose React or Next.js only if the brief needs accounts, payments, a dashboard or a CMS, and say why. Protect the architecture: if I paste a component written for a different stack (for example a React component from 21st.dev into a static site), rebuild its behaviour natively rather than changing how the whole project works, and tell me you did.
* Structure first: build every section with real hierarchy (what the eye reads first, second, third) before polishing any of them.
* Copy: write like the business's best employee, not a brochure. Specific nouns and numbers beat adjectives ("Dry-aged ribeye, 16 oz, 60 days, hearth-fired", "Six dishes, one fire"). No "elevate", "unlock", "seamless", "cutting-edge", "in today's fast-paced world". No em dashes in visible copy. Headlines under ten words. Every button names its action.
* Truth: never invent testimonials, client logos, ratings, awards, stats, prices, team members or addresses. Where real content is missing, render a clearly marked placeholder (`[TESTIMONIAL: needs a real quote with permission]`) and list every placeholder at the end of your reply. A fake five-star rating on a live site is a lie with a URL, not a design detail.
* Imagery: real photography of the actual business beats everything. If I have none, you may write prompts for an image or video model (Nano Banana, GPT Image, Veo, Kling, Higgsfield, etc.) that match `DESIGN.md`, and I will generate the assets. Never generate images of real people, real customers or real before/after results and present them as real. If no good assets exist, choose a typographic or illustrative design that needs none; a strong type-led page beats a fake cinematic one.
* Hero video and scroll-scrubbed video are allowed only if (a) the brand is experiential (hospitality, product, architecture, portfolio), (b) we have a real or deliberately commissioned asset, and (c) it stays under 3 MB with a poster image and a static fallback. For credibility products (health, finance, legal, research, tools) do not use them: the content is the hero.
* Performance: target Lighthouse 90+ on mobile for performance, accessibility, best practices and SEO. Lazy-load below-the-fold media, set explicit image dimensions, preload the display font, no layout shift.
* SEO and sharing basics: unique title and meta description, one h1, Open Graph image, favicon, `sitemap.xml`, `robots.txt`, alt text that describes the image rather than repeating keywords.

## Phase 4: Look at it (the screenshot loop)

After each build, start the local server and capture every section at 390px and 1440px, in light and dark mode if both exist. Compare against `DESIGN.md` and the references. Fix mismatches. Do this twice. Then stop and show me.

For animated backgrounds, canvas or scroll effects, do not use the screenshot loop to judge the animation; a still frame will send you into an over-engineering loop. Implement it, verify it runs without console errors, and ask me to review it.

## Phase 5: Grade yourself honestly

Grade the page against these eight criteria, each as strong, mixed or missing, with one sentence of evidence each. Be harsh; I would rather hear it from you than from a visitor.

1. Point of view: could this site belong to anyone else?
2. Typography: a real pairing and scale, or defaults?
3. Colour: restrained, on-brand, accessible?
4. Hierarchy: does every section say what to read first?
5. Imagery: real, specific and earned, or generic?
6. Motion: purposeful and restrained, or decorative?
7. Mobile: designed for the phone, or the desktop squeezed?
8. The invisible stuff: speed, focus states, forms that work, 404 page, no console errors, favicon, sharing card.

Then propose one batch of fixes for everything mixed or missing. I will reply with intent, not specs ("the lower sections feel generic; make them more expensive, not busier"). Translate intent into specific changes and ship them as one batch.

## Phase 6: The polish pass (I drive, you execute)

I will scroll and name the sections that feel flat. For each one, add exactly one restrained interaction (a hairline that draws in, a cursor-following light, a word-by-word headline reveal, a hover state with weight). One per section, never all of them everywhere. When I say "more subtle", reduce amplitude, slow it down or add easing lag; do not swap it for a different effect.

Then do a dedicated mobile pass: decide what hides, what tightens and what resizes on small screens. Collapse the nav, tighten the wordmark, use smaller button variants, stack instead of shrink, make tap targets at least 44px.

## Phase 7: Verify before shipping

Automate what can be automated and look at the rest:

* A browser QA script (Playwright) at 390px and 1440px, both colour schemes, that fails loudly on: any text contrast below 4.5:1, tap targets under 44px checked with `elementFromPoint` hit-testing rather than measured boxes, horizontal overflow, console errors, broken links, images without alt text, and any CSS colour or font not declared in `DESIGN.md`.
* Keyboard-only walk-through: every interactive element reachable with a visible focus ring.
* `prefers-reduced-motion` on: nothing moves that does not have to.
* Forms submit and fail gracefully, with errors that name the problem and the fix.
* The empty and slow states: throttle to slow 3G and look at what a first-time visitor sees.
* Then look at the screenshots yourself. Passing checks is not the same as looking good; most flaws that matter are only found by looking.

## Phase 8: Ship

Only when I say "ship it":

* Static site: push to a GitHub repo and connect it to Vercel or Netlify for auto-deploy on push, or upload to shared hosting (zip the files inside the project folder, not the folder itself, so `index.html` sits at the top level). Add the custom domain and HTTPS.
* Keep working on localhost afterwards. Changes reach the live site only when I say so; consider a `dev` branch.
* If there is a backend (contact storage, admin login, auth), use a managed service (for example Supabase for data and auth, Resend for email), keep keys in environment variables, add rate limiting to every form, and run a security review before launch: exposed keys, open database rules, unauthenticated admin routes.

## What not to do

Each of these shows up in the tutorials' own output and had to be fixed or should have been:

* Do not start coding from a one-line prompt. Interview first, design system second, code third.
* Do not use Inter, a purple-to-blue gradient, glassmorphism, three identical feature cards, or emoji as icons by default.
* Do not invent stats, testimonials, ratings, client logos or "trusted by" rows. Placeholders, clearly marked, until real ones exist.
* Do not generate a cinematic hero video just because you can. That look is bought with real photography, film or 3D; copying the surface without the asset is what "AI slop" means.
* Do not animate every section, hijack scroll, or add a cursor effect site-wide.
* Do not let the screenshot loop judge animations, and do not let it run forever.
* Do not drop a component from another framework into this stack; rebuild it natively.
* Do not treat "responsive" as "designed for mobile".
* Do not push to production without my explicit go-ahead, and never commit secrets.
* Do not optimise against Lighthouse or linters and call it design. Scores measure tidiness, not taste.

## Adapting this

* Claude Design / claude.ai instead of Claude Code: keep the BRIEF, Phases 1, 2, 5 and 6, the Truth rules and What not to do. Export the design system and hand it to Claude Code for Phases 3, 4, 7 and 8.
* Lovable, v0, Bolt, React and Tailwind: keep everything except the Stack bullet. Put `DESIGN.md` tokens into the Tailwind config and ban arbitrary values outside it.
* A product or tool rather than a brochure site (like Claim Checker): the front page must show the product's real work, not a landing page. Replace the hero with live, server-rendered evidence of what the product does, and drop hero video entirely.
