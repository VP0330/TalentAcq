# Fit labels

The fit label is computed from the requirements table. It is never a judgment call, and nothing written in the resume can change it. The automatic check recomputes it from the table and rejects the file if it differs.

## Rules

Count only rows whose Type is `must`.

- **Strong match:** every must-have is `Shown`.
- **Possible match:** every must-have is `Shown` or `Partly shown`, or at most one must-have is `Not shown in the resume`.
- **Weak match:** two or more must-haves are `Not shown in the resume`.

Also:

- Nice-to-haves never lower a label. A nice-to-have that is Shown belongs under Strengths.
- If the requirements list has one must-have or none, apply the rules literally. With no must-haves, every must-have is (trivially) Shown, so the label is Strong match. Show the header line literally as `Requirements met: 0/0 must-haves shown` (never "N/A").
- The line `Requirements met: X/Y must-haves shown` counts only `Shown` must-haves in X (not `Partly shown`). Y is the number of must-have rows.
- Get the table right first, then read the label off these rules. Never adjust a status to reach a label.

## Worked examples

**Example 1: Strong match.** Four must-haves, all Shown; one nice-to-have Not shown.

| Must-have statuses | Nice-to-have statuses |
|---|---|
| Shown, Shown, Shown, Shown | Not shown in the resume |

Every must-have is Shown, so `Fit: Strong match` and `Requirements met: 4/4 must-haves shown`. The missing nice-to-have does not lower the label.

**Example 2: Possible match.** Five must-haves: three Shown, one Partly shown, one Not shown.

| Must-have statuses |
|---|
| Shown, Shown, Shown, Partly shown, Not shown in the resume |

Not every must-have is Shown, so it is not Strong. Only one must-have is Not shown, so it is `Fit: Possible match`. X counts only Shown rows: `Requirements met: 3/5 must-haves shown`.

**Example 3: Weak match.** Four must-haves: one Shown, one Partly shown, two Not shown; two nice-to-haves Shown.

| Must-have statuses | Nice-to-have statuses |
|---|---|
| Shown, Partly shown, Not shown in the resume, Not shown in the resume | Shown, Shown |

Two must-haves are Not shown, so `Fit: Weak match` and `Requirements met: 1/4 must-haves shown`. The two Shown nice-to-haves go under Strengths; they do not raise the label. The write-up states the gaps as facts about the resume and still suggests screen questions aimed at them.
