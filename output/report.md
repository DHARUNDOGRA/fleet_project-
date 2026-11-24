# Fleet Fuel Efficiency and Maintenance - Findings

## Summary measures (clean data, Jan-Jun 2026)
| Measure | Value |
|---|---|
| Fleet fuel efficiency | **10.45 km/L** (137,388 km on 13,147 L) |
| Fleet operating cost per km (fuel + maintenance) | **Rs 14.11** (fuel Rs 9.09 + maintenance Rs 5.02) |
| Records flagged for verification | **4.8%** (60 of 1248 trip and fuel records) |

## What the data cleaning changed
- Removed **28** duplicate fuel entries and converted mixed units (gallons to litres, miles to km).
- Flagged 7 backward odometer readings, 11 odometer jumps, 8 distance/odometer mismatches and 2 zero-distance trips.
- Flagged 2 fuel entries above tank capacity and 4 price outliers.
- Without cleaning, the fleet would appear to run at **8.74 km/L**; after cleaning it is **10.45 km/L**.
  The raw figure is wrong because it mixes miles with km, gallons with litres, and counts duplicate fills.

## Interpretation
- **Most efficient vehicle:** V01 (Tata Ace Gold) at 16.5 km/L.
- **Furthest below its rated efficiency:** V04 (Mahindra Bolero Pik-Up) reaches only 65% of its rated 14.0 km/L.
- **Cheapest to run:** V01 at Rs 7.44/km. **Most expensive:** V07 at Rs 23.43/km.
- **Highest maintenance burden:** V07 at Rs 10.70/km.
- **Vehicles needing attention:** V04, V06, V07.
- **Highest estimated route cost:** Nashik-Ahmednagar at about Rs 14.53/km (driven by which vehicles run it, see limitations).

## Limitations
- Fuel is bought in fill-ups, so month-level km/L is noisy; the full-period figure per vehicle is more reliable.
- Flagged records are excluded from the calculations, which slightly understates distance for vehicles with odometer problems.
- Route cost is an estimate: each trip is charged at its vehicle's average cost per km, so route differences reflect which vehicles run them.
