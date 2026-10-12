# Persistent game-automation handling rule

- Before any task input, treat the random “攻城令发布/前往” notice as a blocker and never click its “前往” control.
- If the notice identity is unambiguous and that underlying page has a verified blank-area dismissal target outside the notice and outside interactive controls, an execute task may click that target once, capture a fresh screenshot, and continue only after the notice is gone and the intended page is re-identified.
- Calibrate the blank target for each underlying page context. Do not reuse a target from another page or fall back to a guessed coordinate. If no verified blank target exists, stop without task input and request manual dismissal or fresh evidence.
- A dry-run remains read-only and never dismisses notices. A dismissal attempt is separate from the requested task action and must be recorded in the run evidence.
