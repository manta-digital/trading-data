---
docType: notes
project: trading-data
dateCreated: 20261113
dateUpdated: 20261113
status: complete
---

# 308 tick-bar measurement (criterion 12)

- Request: `http://127.0.0.1:8100/api/v1/futures/products/ES/tick-bars`
  `ticks_per_bar=1000 rule=v.0`
  `start=2024-09-09 end=2024-09-13`
- Sessions: 4; ledger record sum 1,967,590
  (98.4% of the 2,000,000 trade limit)
- Request time: 5.519 s (bound 10.0 s)
- `/futures/products` idle: [0.297, 0.295, 0.291, 0.328, 0.264, 0.267, 0.285]
  (median 0.291 s)
- During the request: [0.464, 0.405, 0.41, 0.413, 0.42, 0.378, 0.401]
  (margin 0.25 s)

## Checks

- every check passed
