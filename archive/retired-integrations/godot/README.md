# Retired Godot integration

Godot support was retired after Astrolabe commit `29acd6a` to reduce the integrations the sole
maintainer has to develop and maintain. This was a product-scope decision, not a claim that Godot is
unavailable, untestable, or unsuitable for Astrolabe.

This directory preserves the last implementation for a future contributor:

- `plugin/trackball_nav/`: EditorPlugin payload, version `0.1.14`;
- `docs/godot.md`: maintainer guide and known camera constraints;
- `tools/godot_parse_check.py`: real-editor GDScript parse check;
- `tests/test_integrations_godot.py`: installer and project-discovery tests.

The archived code is not packaged, installed, imported, or exercised by the active test suite.
At retirement, integration metadata described Godot 4.4 through 4.7 as the verified host range.

Revival requires treating this as a new support decision: restore the canonical app registry entry,
packaged profiles and defaults, setup/detection/compatibility metadata, payload release checks,
active tests, user/security/feature documentation, and live project/viewport verification. Run the
archived parse check against the intended editor versions before moving the payload back into
`trackball_daemon/plugins/`.
