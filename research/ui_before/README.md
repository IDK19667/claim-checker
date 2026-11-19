# UI before the rebuild (main 5877d9b)

Produced by `scripts/ui_measure.mjs` against `scripts/ui_fixture_server.py`
(no key, no network, replayed stages). Top is page coordinates
(`getBoundingClientRect().top + scrollY`) at scroll 0, measured 2.5 s after
load. "On first screen" means fully inside the first viewport with no scroll.
`measure.json` has the raw numbers.

| Width x height | Motion | Input top (px) | Submit bottom (px) | Input + submit on first screen | Page height (px) | Horizontal overflow |
|---|---|---|---|---|---|---|
| 1440x900 | on | 8067 | 8213 | no (input no, submit no) | 10701 | no |
| 1440x900 | reduce | 3051 | 3198 | no (input no, submit no) | 5686 | no |
| 768x1024 | on | 8782 | 8928 | no (input no, submit no) | 11468 | no |
| 768x1024 | reduce | 3190 | 3337 | no (input no, submit no) | 5876 | no |
| 390x844 | on | 825 | 972 | no (input no, submit no) | 4009 | no |
| 390x844 | reduce | 825 | 972 | no (input no, submit no) | 4009 | no |
| 375x812 | on | 865 | 1012 | no (input no, submit no) | 4142 | no |
| 375x812 | reduce | 865 | 1012 | no (input no, submit no) | 4142 | no |

Reading the table: on a phone the claim box starts just below the fold
(825 px on a 844 px screen, 865 px on 812) and its button is further down.
On desktop and tablet with motion on, the scroll-driven fly-through sits above
the checker, so the input is 8000+ px down the page; under reduced motion it is
about 3000 px down.

Screenshots in this folder (PNG, or JPEG q70 when a PNG passed 400 KB):
home-first-{1440,768,390,375}, home-first-390-reduced, home-full-{1440,390};
then at 1440 and 390: error-empty-submit, loading, result, result-full,
source-dialog, error-network, error-provider.
