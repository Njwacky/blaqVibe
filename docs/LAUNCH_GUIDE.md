# Launch Guide: problem analysis and maintenance

This document records why the in-product Launch Guide is structured as a destination router instead of another upload button. The running implementation lives in `gallery/launch_guides.py`, `gallery/launch_views.py`, and `templates/gallery/launch_*.html`.

## Five Whys

1. **Why do creators finish an app but not release it?**
   They do not know where the app belongs or what the destination accepts.

2. **Why does a generic “upload your project” answer fail?**
   A static-site host needs built files, a server host needs a working runtime, mobile stores need signed builds and metadata, and game/desktop stores need platform packages and review preparation. Source code is not a universal release artifact.

3. **Why is a list of platform logos still not enough?**
   A first-time publisher cannot see the hidden prerequisites: signing identities, permanent package IDs, environment variables, testing tracks, store assets, policy declarations, fees, and review timing.

4. **Why can a command list make the situation worse?**
   Framework build/start commands are project-specific. A plausible-looking placeholder can waste time or produce an unsafe deployment. Only commands supported by the destination’s official documentation belong in the guide; project-specific commands must point creators back to their framework documentation.

5. **Why might creators believe they already hosted the app?**
   BlaqVibes displays sandboxed snippets and file previews. Without a clear boundary, “preview” can look like “deployed,” even though BlaqVibes does not run a production backend or submit a store build.

### Root problem

The missing capability is **release-path confidence**, not file transfer. A creator needs to identify the artifact they have, choose a compatible destination, see prerequisites before committing, and complete a trustworthy handoff to that platform.

### Product decisions derived from the analysis

- Start with an artifact-based router, then show destinations.
- State that BlaqVibes preview is not production hosting on the hub and contextual upload/project pages.
- Separate static web, framework web, continuously running servers, and container registries.
- Branch games into a direct storefront (itch.io) and a reviewed PC storefront (Steam).
- Branch desktop guidance by Windows, direct macOS, and Linux/Flathub workflows.
- Keep Android and Apple signing, testing, metadata, and review steps explicit.
- Display angle-bracket placeholders conspicuously and never invent framework-specific commands.
- Link primary official documentation on every guide and tell readers the live platform is the final authority.
- Store optional completion state only in browser local storage; never request store credentials.

## Source and command policy

1. Platform-owner documentation is the primary source. Engine documentation can supplement an engine-specific export note.
2. Commands are included only when the official destination documentation provides that workflow and the command remains generally applicable.
3. Angle brackets mean “replace this value.” No example token should look like a production credential or fixed project name.
4. Dashboard labels, account rules, pricing, waiting periods, package formats, and review policies must be checked at maintenance time.
5. Docker Hub is always described as an image registry, not an app host.
6. A PWA is described as a hosted website with installability metadata, not as automatic app-store distribution.
7. Apple’s App Store route and direct notarized macOS distribution stay separate.

## Maintenance checklist

- Review every URL and claim in `gallery/launch_guides.py` at least quarterly.
- Update `LAST_REVIEWED` only after opening all source pages and checking commands and dashboard terminology.
- Run the route, source-scheme, placeholder, content-boundary, and rendering tests in `gallery/tests.py`.
- Spot-check all desktop and mobile layouts, keyboard focus, copy buttons, artifact recommendations, and local checklist state.
- Never add credentials, affiliate links, or claims that BlaqVibes performs the external submission.
