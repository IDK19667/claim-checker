# axe-core results (axe-core from a scratchpad npm install, not a project dependency)

Run with playwright against the fixture server; violations only. Result page is /?q=Vitamin C prevents the common cold.

| Page | Width | Serious/critical before fix | Serious/critical after fix | Other violations after fix |
|---|---|---|---|---|
| home | 1440 | 0 | 0 | aria-allowed-role (minor) |
| result | 1440 | 1 | 0 | page-has-heading-one (moderate) |
| source-dialog | 1440 | 0 | 0 | none |
| error | 1440 | 0 | 0 | page-has-heading-one (moderate) |
| about | 1440 | 0 | 0 | none |
| method | 1440 | 0 | 0 | none |
| corrections | 1440 | 0 | 0 | none |
| privacy | 1440 | 0 | 0 | none |
| home | 390 | 0 | 0 | aria-allowed-role (minor) |
| result | 390 | 1 | 0 | page-has-heading-one (moderate) |
| source-dialog | 390 | 0 | 0 | none |
| error | 390 | 0 | 0 | page-has-heading-one (moderate) |
| about | 390 | 0 | 0 | none |
| method | 390 | 0 | 0 | none |
| corrections | 390 | 0 | 0 | none |
| privacy | 390 | 0 | 0 | none |

Fixed: label-content-name-mismatch (serious) on the study bar buttons of the result page (8 nodes). The accessible name now starts with the visible text: spine text then the study number, for example 'Meta-analysis 01, 2017, used for this verdict'. A space now separates the spine and the number in the markup. Changed in templates/index.html and renderChart in static/app.js; CACHE bumped to v87.

Remaining, not serious: aria-allowed-role (minor) on #claim-input (role=combobox on the input, home and error), page-has-heading-one (moderate). Neither is serious or critical, so left alone.

Total serious/critical after fix: 0
