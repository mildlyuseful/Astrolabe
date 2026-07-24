# Security policy

## Reporting a vulnerability

Report privately through GitHub, not in a public issue, discussion, or pull request:

**<https://github.com/mildlyuseful/Astrolabe/security/advisories/new>**

That form is the only reporting channel today. A Mildly Useful security mailbox will be added here
once the domain and mailbox exist; until then, GitHub private vulnerability reporting is what gets
the report to the maintainer.

Astrolabe is maintained by one person and has no committed response-time target. Reports are read
and triaged as soon as reasonably possible, and you will get an acknowledgement when the report is
picked up. If a report goes unanswered longer than you consider reasonable, treat coordinated
disclosure as the courtesy it is and use your own judgement.

## Do not publish sensitive material with your report

A useful report often contains material that should not be public. Send it through the private
advisory, and keep it out of public issues, screenshots, videos, and gists:

- certificates, private keys, or certificate stores — including the per-user Onshape bridge
  certificate;
- BLE device addresses, pairing material, or device identifiers;
- absolute paths that contain your Windows account name or other personal directories;
- daemon, add-on, or host logs that have not been reviewed for the items above;
- crash dumps and memory captures.

Redact rather than omit where you can, and say what you redacted.

## What is supported

Only the latest public release receives security fixes. Older releases, prereleases, and builds
made from a source checkout do not.

There is no public release yet. Astrolabe is pre-alpha, so today every build is a source or private
build and nothing carries a security-support claim. This section becomes meaningful with the first
public release, and the supported version will be named here at that point.

Support tier also matters. A supported integration's security defect is release-blocking. An
experimental integration does not block release for an isolated functional regression, but it does
block release for a security, data-loss, configuration-corruption, or lifecycle defect, because
those are shared with the rest of the product.

## Scope

In scope:

- the daemon and its packaged Python distribution;
- the loopback navigation broker, the Onshape TLS bridge, and their certificate and trust handling;
- the Windows Raw Input path, single-instance handling, and start-at-login behavior;
- host integration setup, update, and reversal — including files copied into 3D applications, the
  AutoCAD trusted-path and NETLOAD flow, and the Rhino startup-command registration;
- the release artifacts, once signed artifacts and an installer exist.

Out of scope, unless Astrolabe makes it materially worse:

- vulnerabilities in the 3D host applications, browsers, or Windows itself;
- vulnerabilities in third-party dependencies, which should go to that project first — tell us too
  if Astrolabe's use of it is what makes it exploitable;
- the absence of code signing on development builds, and the resulting SmartScreen, Defender, and
  browser certificate warnings. Those are expected for an unsigned pre-alpha build and are described
  in [`docs/security.md`](docs/security.md);
- physical attacks that require an attacker to already control the machine or the trackball.

## What the product does to your machine

[`docs/security.md`](docs/security.md) is the current inventory: which listeners bind where, which
permissions are requested, what each integration writes outside the application directory, and how
to reverse every one of them. Read it before treating an observed change as unexpected — and please
say so in a report if you find that inventory incomplete, because an undocumented change is itself a
defect worth reporting.
