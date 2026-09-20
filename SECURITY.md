# Security Policy

Security is the entire point of farabunker, so we take vulnerabilities seriously — in the
platform itself and in the guidance we give operators.

## Reporting a vulnerability

**Please do not report security issues in public GitHub issues, pull requests, or
discussions.** Public disclosure before a fix puts every operator at risk.

Report privately through **GitHub's private vulnerability reporting**: open this repository's
*Security* tab and choose **Report a vulnerability**. That is the only reporting channel, and
it reaches the maintainers directly. If the *Security* tab shows no "Report a vulnerability"
button, open an issue titled "security contact request" with no details, and a maintainer
will reply with a private channel.

Please include: a description of the issue, steps to reproduce or a proof of concept, the
affected component/version, and the potential impact. If you'd like, tell us how you wish to
be credited.

## What to expect

- **Acknowledgement** within 5 business days.
- An assessment within 14 days and, if confirmed, a fix plan with a coordinated disclosure
  timeline — 90 days by default, sooner where a fix ships sooner.
- Credit for your responsible disclosure, if you want it.

## Safe harbour

We will not pursue or support legal action against anyone who reports in good faith through
the channel above, stays within their own systems and data, avoids privacy violations and
service degradation, and gives us reasonable time to fix the issue before disclosing it.

## Out of scope

- Findings against a box you do not operate, or testing against someone else's install.
- Missing security headers, TLS configuration, or rate limits on a LAN-only deployment, where
  the posture model puts the boundary at the network edge rather than the app.
- Self-XSS, clickjacking on unauthenticated pages, and reports produced solely by an automated
  scanner with no demonstrated impact.
- Social engineering, physical attacks, and denial of service by resource exhaustion.

## Scope notes specific to farabunker

Because farabunker's threat model is *"assume the network is compromised,"* the following are
of particularly high interest:

- **Egress leaks** — any path by which the platform or a module can reach the public internet
  in a posture profile that forbids it (breaking default-deny egress).
- **Module sandbox escapes** — a module gaining capabilities it did not declare, reaching the
  host, or pivoting to another module.
- **Airlock bypass** — accepting an unsigned, tampered, or unverified update package.
- **Capability-grant violations** — the core handing a module authority beyond its manifest.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md#2-security-posture-the-offline-spectrum) for
the security model these map to.

## Supported versions

The project is pre-1.0 and has no tagged releases yet, so there is no supported-versions
table to publish: **`main` is the supported version.** Report against `main`, and a table
will appear here with the first release.
