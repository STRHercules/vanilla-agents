"""Stage 2: the trusted verification runner (standard library only; needs git on PATH).

Pure parts: manifest, patchcheck, diffint, redact, rules, bundle.  Effects: checkout, runner, verifier, client.
Security posture is unchanged (level_0_integrity_checks_only): files are same-user writable, so the manifest DETECTS changes, it cannot prevent them.
"""
VERIFIER_ID = "vanilla-verifier"
VERIFIER_VERSION = "0.2.0-stage2"
