# Onshape permission live evidence — 2026-09-05

- Reporter: Dylan, testing on the same Windows computer after the installation reset and backup.
- Test context: the patched source checkout on `fix/new-machine-first-run`, with the Astrolabe
  cursor userscript enabled in Violentmonkey. The prepared script is version `0.2.1`; the report
  did not independently capture the loaded script or browser version.
- Exercise: open Onshape with fresh permission state and accept the local-device permission.
- Result: Dylan reported that a single permission window appeared and could be accepted. The
  accompanying screenshot shows the `cad.onshape.com` request with Allow and Block buttons.
  The previously reported continuous flicker no longer prevented acceptance in this test.
- Scope: user-reported live browser permission behavior. This does not qualify a rebuilt installer,
  all browser families, denial/revocation, or pointer recovery after a daemon restart. Remaining
  verification is tracked in [`../../TODO.md`](../../TODO.md).
