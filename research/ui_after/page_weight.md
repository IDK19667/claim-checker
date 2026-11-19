# Page weight, before (main 5877d9b) vs after (ui-rebuild)

Fixture server, cold load, 10 s idle, no scroll. Script: scripts/page_weight.mjs (result page via a path override in a scratch copy).

## before: /
| viewport | requests | MB | frame/bundle requests | load @ Fast 3G |
|---|---|---|---|---|
| 375x812 phone | 31 | 0.60 | 0 | 1.5 s |
| 375x812 plain viewport (mouse) | 31 | 0.60 | 0 | 1.6 s |
| 1440x900 desktop | 35 | 0.57 | 2 | 1.2 s |

## before: /?q=Vitamin%20C%20prevents%20the%20common%20cold
| viewport | requests | MB | frame/bundle requests | load @ Fast 3G |
|---|---|---|---|---|
| 375x812 phone | 34 | 1.05 | 0 | 1.5 s |
| 375x812 plain viewport (mouse) | 26 | 0.27 | 0 | 1.6 s |
| 1440x900 desktop | 26 | 0.27 | 0 | 1.5 s |

## after: /
| viewport | requests | MB | frame/bundle requests | load @ Fast 3G |
|---|---|---|---|---|
| 375x812 phone | 23 | 0.17 | 0 | 1.2 s |
| 375x812 plain viewport (mouse) | 23 | 0.17 | 0 | 1.1 s |
| 1440x900 desktop | 23 | 0.17 | 0 | 1.2 s |

## after: /?q=Vitamin%20C%20prevents%20the%20common%20cold
| viewport | requests | MB | frame/bundle requests | load @ Fast 3G |
|---|---|---|---|---|
| 375x812 phone | 23 | 0.18 | 0 | 1.2 s |
| 375x812 plain viewport (mouse) | 23 | 0.18 | 0 | 1.2 s |
| 1440x900 desktop | 23 | 0.18 | 0 | 1.2 s |
