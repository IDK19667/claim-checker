# Publishing Claim Checker

Everything in the repo is ready. What is left needs an account, which is
why you have to do these parts rather than me.

Budget: about 20 minutes, £0.

---

## Before you start

Two things that are already true and worth knowing:

- **Your API key is not in the repo.** `.gitignore` excludes `.env`, and the
  first commit was checked for key-shaped strings before it was made. The
  key goes into Render's dashboard instead, where it is encrypted.
- **The database is not in the repo either.** That means the site launches
  with an empty front page: no ledger counts, no latest checks. See
  "Make it look alive" below, which takes two minutes.

---

## 1. Put the code on GitHub

Create an empty repository at <https://github.com/new>. Do not let it add a
README, a licence or a `.gitignore`, since the repo already has them.

Then, in this folder:

```bash
git remote add origin https://github.com/YOUR-USERNAME/claim-checker.git
git branch -M main
git push -u origin main
```

If it asks for a password, use a personal access token, not your GitHub
password: <https://github.com/settings/tokens>

## 2. Deploy on Render

1. Sign up at <https://render.com> with your GitHub account.
2. **New** → **Blueprint**, and pick the repository you just pushed.
   Render reads `render.yaml` and fills in the service for you.
3. It will ask for the values marked `sync: false`. Set:
   - `GEMINI_API_KEY` — the key from your local `.env`
   - `NCBI_EMAIL` — your email. PubMed asks callers to identify themselves;
     it is a courtesy, not a requirement.
   - `PUBLIC_URL` — leave blank for now, you do not know the URL yet.
4. Deploy. The first build takes a few minutes.
5. When it is live, copy the URL Render shows you, then go to
   **Environment**, set `PUBLIC_URL` to exactly that, and let it redeploy.

   The blueprint names the service `evident`, but both
   `evident.onrender.com` and `claim-checker.onrender.com` are already
   registered to other people's services, so Render will hand you the name
   with a suffix on it, something like `evident-a1b2.onrender.com`. Copy
   what it actually gives you rather than what you expected.

`PUBLIC_URL` matters more than it looks: share links, link-preview cards
and the ClaimReview markup all build absolute URLs from it. Left blank,
shared verdicts point at the wrong place.

## 3. Check it works

```bash
curl -s https://YOUR-URL.onrender.com/healthz
```

Then open the site and check one claim end to end. Watch for:

- the verdict, the takeaway, and the "still open" line
- the studies listed underneath, and a deep dive when you open one
- the share card, which should carry your domain in its footer

## 4. Make it look alive

The front page is built from real counts, so on a fresh deploy it reads
zero. Check eight or ten claims yourself. At two Gemini requests each that
is roughly twenty requests, which is nothing, and the ledger, the latest
checks and the trending list all start working.

Good ones to seed with, because people actually search them: apple cider
vinegar and diabetes, sunscreen and cancer, turmeric and inflammation,
vaccines and autism, blue light and eyes, magnesium and sleep.

## 5. A custom domain, if you want one

In Render: **Settings** → **Custom Domain**, then follow its DNS
instructions at your registrar. HTTPS is issued automatically. Afterwards
update `PUBLIC_URL` to the new domain and redeploy.

---

## Things that will surprise you

**The free instance sleeps.** After about 15 minutes idle it spins down,
and the next visitor waits roughly 30 seconds for it to wake. That is the
price of £0. A paid instance removes it.

**The cache is wiped on every restart and deploy.** Free instances have an
ephemeral filesystem. Nothing breaks: claims are simply re-checked and the
counts start climbing again. When that starts to bother you, uncomment the
`disk:` block in `render.yaml`, move to a paid instance, and set
`DB_PATH=/var/data/health_claim_checker.db`.

**Python version.** `render.yaml` pins 3.12.7 because it is reliably
available. You develop on 3.14 and nothing in the code needs anything newer
than 3.10, but if the build complains, raise `PYTHON_VERSION` to match your
local one.

**Rate limits are per instance.** `RATE_LIMIT_GLOBAL_PER_DAY` defaults to
400 checks a day, which is the app throttling itself so it never surprises
you with a quota error. Once you can see your real Gemini limits in AI
Studio, set this to match them.

---

## Afterwards

Deploying is what makes the SEO work stop being theoretical. Once you have
a URL:

- submit `https://YOUR-URL/sitemap.xml` at
  <https://search.google.com/search-console>
- check a verdict page with the Rich Results test to confirm the
  ClaimReview markup is read: <https://search.google.com/test/rich-results>
- paste a shared link into WhatsApp or iMessage and confirm the preview
  card renders

## Updating the site later

```bash
git add -A
git commit -m "what changed"
git push
```

Render redeploys on push. Keep working against `localhost:5000`; nothing
reaches the live site until you push.
