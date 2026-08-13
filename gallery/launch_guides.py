"""Curated, source-backed publishing guides for the Launch guide pages.

This is intentionally data, not user-generated content. Commands are copied from the
linked official documentation and placeholders are visibly marked. Dashboard wording
and requirements should be rechecked against those sources when this file is updated.
"""

LAST_REVIEWED = "13 August 2026"

CATEGORIES = (
    {"slug": "all", "label": "All destinations"},
    {"slug": "web", "label": "Web"},
    {"slug": "mobile", "label": "Mobile"},
    {"slug": "games", "label": "Games"},
    {"slug": "desktop", "label": "Desktop"},
    {"slug": "distribution", "label": "Other"},
)

COMMON_SAFETY = (
    "Build and test the production artifact—not only the editor or development preview.",
    "Remove .env files, private keys, test accounts, and API secrets. Put runtime secrets in the host or store dashboard instead.",
    "Confirm you own or may distribute every image, font, sound, library, and other asset.",
    "Prepare a support contact, privacy policy, and account-deletion flow when your app collects user data or creates accounts.",
    "Check the destination’s current pricing, country availability, and policy requirements before paying or promising a release date.",
)

LAUNCH_GUIDES = (
    {
        "slug": "cloudflare-pages",
        "category": "web",
        "icon": "◇",
        "eyebrow": "Static web",
        "title": "Put a static site on Cloudflare Pages",
        "summary": "For an HTML/CSS/JavaScript site or a framework build that becomes static files.",
        "result": "A public HTTPS pages.dev URL, with an optional custom domain",
        "artifact": "A build-output folder containing index.html and its assets",
        "time": "Usually the quickest web path",
        "good_for": ("Landing pages", "Portfolios", "Static React/Vue builds", "Browser game exports"),
        "not_for": "A server process, private database, background worker, or code that must run continuously. Use the server-app guide instead.",
        "prerequisites": (
            "A Cloudflare account.",
            "A tested output folder with a top-level index.html.",
            "The project’s real build instructions, if source code must be compiled first.",
        ),
        "steps": (
            {
                "title": "Find the deployable folder",
                "body": "Open the project documentation or package scripts and run its documented production build. Upload the resulting folder—not node_modules and not an unbuilt source folder. Confirm index.html is at that folder’s top level.",
                "checks": ("Open the built site with a local static server.", "Test a hard refresh on any client-side routes."),
            },
            {
                "title": "Choose Git integration or Direct Upload",
                "body": "Git integration rebuilds after repository pushes. Direct Upload accepts a prebuilt folder from the dashboard or Wrangler. Cloudflare says a Direct Upload project cannot later be switched to Git integration, so decide before creating it.",
            },
            {
                "title": "Create a Direct Upload project",
                "body": "In Workers & Pages, choose Create application → Get started → Drag and drop your files, enter a project name, and upload the output folder or a ZIP. For the official CLI route, authenticate and create the Pages project first.",
                "commands": (
                    {"label": "Create the Pages project", "text": "npx wrangler pages project create"},
                ),
            },
            {
                "title": "Deploy the built output",
                "body": "Run the official Wrangler command from the project directory. Replace the angle-bracket placeholder with the real output directory, such as a folder produced by your build tool.",
                "commands": (
                    {"label": "Deploy to Pages", "text": "npx wrangler pages deploy <BUILD_OUTPUT_DIRECTORY>", "replace": "Replace <BUILD_OUTPUT_DIRECTORY>."},
                ),
            },
            {
                "title": "Verify before sharing",
                "body": "Open the generated pages.dev address in a private browser window. Test navigation, forms, mobile layout, missing-file errors, and a hard refresh. Add a custom domain only after the generated URL works.",
            },
        ),
        "checklist": ("Production URL opens over HTTPS", "No secret appears in downloaded JavaScript or source maps", "Deep links survive a hard refresh", "Custom-domain DNS is verified, if used"),
        "sources": (
            {"label": "Cloudflare Pages: Direct Upload", "url": "https://developers.cloudflare.com/pages/get-started/direct-upload/"},
            {"label": "Cloudflare Pages: Git integration", "url": "https://developers.cloudflare.com/pages/get-started/git-integration/"},
            {"label": "GitHub Pages: create a site (alternative)", "url": "https://docs.github.com/en/pages/getting-started-with-github-pages/creating-a-github-pages-site"},
        ),
    },
    {
        "slug": "vercel-web",
        "category": "web",
        "icon": "▲",
        "eyebrow": "Frontend & web frameworks",
        "title": "Deploy a web project with Vercel",
        "summary": "For supported frontend frameworks and web projects that Vercel can detect and build.",
        "result": "A public HTTPS deployment; a new project’s first CLI deployment is production",
        "artifact": "A project directory or linked Git repository",
        "time": "Fast when the framework is supported",
        "good_for": ("Next.js", "React frontends", "Framework sites", "Preview deployments"),
        "not_for": "An arbitrary always-running server or worker. Confirm Vercel supports your framework and runtime before choosing this route.",
        "prerequisites": (
            "A Vercel account and a supported project.",
            "A production build that succeeds locally using the project’s own instructions.",
            "A list of required environment-variable names and production values.",
        ),
        "steps": (
            {
                "title": "Make the project production-ready",
                "body": "Run the project’s documented build and fix all errors. Keep secrets out of the repository and browser bundle. Commit the source, framework configuration, and dependency lockfiles the build needs; do not commit local build output unless the framework’s own deployment guide requires it.",
            },
            {
                "title": "Install the official CLI and link the directory",
                "body": "Run these official Vercel CLI commands in the project directory. The link flow associates the local directory with a Vercel project.",
                "commands": (
                    {"label": "Install Vercel CLI", "text": "npm i -g vercel"},
                    {"label": "Link this directory", "text": "vercel link"},
                ),
            },
            {
                "title": "Configure production settings",
                "body": "In Project Settings, verify the detected framework, root directory, build command, and output directory. Add secrets and environment variables in Vercel’s dashboard; do not upload .env files. Use values for the correct Preview or Production environment.",
            },
            {
                "title": "Create and test the first deployment",
                "body": "Run the deploy command and open the returned URL. Vercel’s current CLI documentation says the first deployment of a new project is a production deployment even without --prod; later deployments without --prod are previews. Do not assume the first URL is private. Test authentication, API calls, redirects, error pages, and mobile layout before sharing it.",
                "commands": (
                    {"label": "Create a deployment", "text": "vercel deploy"},
                ),
            },
            {
                "title": "Deploy a tested update to production",
                "body": "After testing the deployment, use the production flag for later production deployments. Verify the production domain separately because production environment variables and domain assignment can differ from previews.",
                "commands": (
                    {"label": "Deploy to production", "text": "vercel deploy --prod"},
                ),
            },
        ),
        "checklist": ("Local production build passes", "Returned deployment URL is tested", "Production variables are configured", "Production domain and redirects work"),
        "sources": (
            {"label": "Vercel CLI", "url": "https://vercel.com/docs/cli"},
            {"label": "Vercel CLI: deploy", "url": "https://vercel.com/docs/cli/deploy"},
            {"label": "Vercel environment variables", "url": "https://vercel.com/docs/environment-variables"},
        ),
    },
    {
        "slug": "render-web-service",
        "category": "web",
        "icon": "⬡",
        "eyebrow": "Backend & full stack",
        "title": "Host a server app on Render",
        "summary": "For an API or full-stack app that needs a runtime, environment variables, and possibly a database.",
        "result": "A continuously addressable HTTPS web-service URL",
        "artifact": "A Git repository or a tested container image",
        "time": "More setup than a static site",
        "good_for": ("Django", "Express", "FastAPI", "Server-rendered apps", "APIs"),
        "not_for": "A folder of static files only; that is simpler and often cheaper on a static-site host.",
        "prerequisites": (
            "A repository the Render account can access.",
            "Your framework’s real production build and start commands.",
            "A server that listens on 0.0.0.0 and the host-provided PORT.",
            "A plan for databases, uploaded files, scheduled jobs, and email services.",
        ),
        "steps": (
            {
                "title": "Prove the production start path locally",
                "body": "Use the production server recommended by your framework—not its development server. Do not copy a guessed universal command: Django, Node, Rails, and other frameworks start differently. Confirm the process reads the PORT environment variable and binds to 0.0.0.0.",
            },
            {
                "title": "Push a clean repository",
                "body": "Commit required source, dependency lockfiles, migrations, and build configuration. Exclude .env, credentials, local databases, uploaded user files, and caches. Make sure a fresh checkout can install and build.",
            },
            {
                "title": "Create the web service",
                "body": "In the Render Dashboard choose New → Web Service, connect the Git provider, and select the repository and branch. Choose the runtime and instance type deliberately.",
            },
            {
                "title": "Enter real build and start commands",
                "body": "Fill in the commands documented by your framework or project. Replace sample project names with your actual module or entry point. Under Advanced, add environment variables and secrets. Create and connect a managed database if the app needs one.",
            },
            {
                "title": "Add health checking and deploy",
                "body": "Set a lightweight health-check path that returns a successful response only when the app can serve traffic. Create the service, watch the Events and logs, and fix the first failing build rather than repeatedly redeploying unchanged code.",
            },
            {
                "title": "Test production state",
                "body": "Test sign-up, login, database writes, email, file persistence, restarts, and error handling. Confirm uploaded files use persistent storage or an object store; an instance filesystem may not be durable. Add a custom domain after the service URL works.",
            },
        ),
        "checklist": ("Server binds to 0.0.0.0 and PORT", "Secrets exist only in dashboard settings", "Database migrations completed", "Health check passes", "Persistent user data survives a redeploy"),
        "sources": (
            {"label": "Render Web Services", "url": "https://render.com/docs/web-services"},
            {"label": "Render: deploy Django", "url": "https://render.com/docs/deploy-django"},
            {"label": "Render health checks", "url": "https://render.com/docs/health-checks"},
            {"label": "Render deploy troubleshooting", "url": "https://render.com/docs/troubleshooting-deploys"},
        ),
    },
    {
        "slug": "installable-pwa",
        "category": "web",
        "icon": "✦",
        "eyebrow": "Installable web app",
        "title": "Make a hosted website installable as a PWA",
        "summary": "A PWA is still a website: host it first, then meet browser installability requirements.",
        "result": "An HTTPS website that supported browsers can install",
        "artifact": "A hosted web app plus a valid web app manifest and icons",
        "time": "A layer on top of web hosting",
        "good_for": ("Web apps", "Home-screen installs", "Desktop browser installs", "Optional offline use"),
        "not_for": "Automatic placement in Google Play or the Apple App Store. A PWA URL and native-store submissions are different distribution routes.",
        "prerequisites": (
            "A working production website served over HTTPS.",
            "A web app manifest linked from the app’s HTML.",
            "App icons, including the sizes required by the browsers you support.",
        ),
        "steps": (
            {
                "title": "Host the web app first",
                "body": "Use the static-site or server-app guide and verify the public HTTPS URL. A ZIP opened from a local drive and a BlaqVibes preview are not production PWA hosting.",
            },
            {
                "title": "Create and link a real manifest",
                "body": "Add a web app manifest with the identity and launch fields required by target browsers. Chromium-based browsers expect name or short_name, suitable icons, start_url, and a display mode; prefer_related_applications must be false or absent.",
            },
            {
                "title": "Supply production icons and scope",
                "body": "Provide correct 192×192 and 512×512 icons for Chromium install promotion and verify every icon URL returns the right image. Confirm start_url and scope keep users inside the intended app path.",
            },
            {
                "title": "Add offline behavior only if you promise it",
                "body": "A service worker can cache an offline shell and data, but poor caching can serve stale code or break sign-out. Implement and test it only when offline behavior is a product requirement; do not claim offline support merely because the app is installable.",
            },
            {
                "title": "Test actual installation",
                "body": "Use browser developer tools to inspect manifest and service-worker errors. Install from a supported desktop browser and at least one target mobile device, launch from the installed icon, update the deployment, and confirm the installed app receives the update.",
            },
        ),
        "checklist": ("Public URL uses HTTPS", "Manifest has no broken icon or start URLs", "Install works on target browsers", "Offline claims are tested with the network disabled"),
        "sources": (
            {"label": "MDN: Making PWAs installable", "url": "https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Guides/Making_PWAs_installable"},
            {"label": "web.dev: install criteria", "url": "https://web.dev/articles/install-criteria"},
        ),
    },
    {
        "slug": "docker-hub",
        "category": "distribution",
        "icon": "▣",
        "eyebrow": "Container registry",
        "title": "Publish a container image to Docker Hub",
        "summary": "Package a tested server as an image that a separate container host or server can run.",
        "result": "A versioned image in a registry—not a public application URL",
        "artifact": "A Dockerfile and working container image",
        "time": "Build path plus a separate hosting path",
        "good_for": ("Portable server builds", "APIs", "Workers", "Self-hosting", "Container platforms"),
        "not_for": "Hosting by itself. Docker Hub stores and distributes images; it does not keep your web app running for visitors.",
        "prerequisites": (
            "Docker installed and a Docker Hub account/repository.",
            "A Dockerfile that does not bake credentials into the image.",
            "A separate server or container host for the final runtime.",
        ),
        "steps": (
            {
                "title": "Build a versioned image",
                "body": "From the directory containing the Dockerfile, run the official build pattern. Replace every angle-bracket placeholder. Prefer a meaningful version tag over relying only on latest.",
                "commands": (
                    {"label": "Build the image", "text": "docker build -t <DOCKER_HUB_USER>/<IMAGE_NAME>:<TAG> .", "replace": "Replace user, image name, and tag."},
                ),
            },
            {
                "title": "Run and test the exact image",
                "body": "Run the tagged image locally with the environment and dependencies it needs. Publish only the necessary container port. Binding a port without a host IP exposes it on all host interfaces by default, so use a loopback bind for local-only testing.",
                "commands": (
                    {"label": "Local-only port test", "text": "docker run --rm -p 127.0.0.1:<HOST_PORT>:<CONTAINER_PORT> <DOCKER_HUB_USER>/<IMAGE_NAME>:<TAG>", "replace": "Replace all four placeholders."},
                ),
            },
            {
                "title": "Authenticate and push",
                "body": "Log in without placing a password in shell history, then push the exact tag you tested.",
                "commands": (
                    {"label": "Sign in", "text": "docker login"},
                    {"label": "Push the image", "text": "docker push <DOCKER_HUB_USER>/<IMAGE_NAME>:<TAG>", "replace": "Replace user, image name, and tag."},
                ),
            },
            {
                "title": "Deploy it on a real runtime",
                "body": "Create a service on a container host or provision your own server, point it at the image tag, add runtime secrets there, map the expected port, configure health checks and persistent storage, and attach a public HTTPS domain. Do not put secrets in image layers or a public registry description.",
            },
        ),
        "checklist": ("Tested tag exists in Docker Hub", "Image contains no credentials", "Runtime host—not Docker Hub—serves the public URL", "Only required ports are exposed", "Persistent state lives outside the disposable container"),
        "sources": (
            {"label": "Docker: build and push an image", "url": "https://docs.docker.com/get-started/introduction/build-and-push-first-image/"},
            {"label": "Docker: publishing container ports", "url": "https://docs.docker.com/engine/network/port-publishing/"},
        ),
    },
    {
        "slug": "google-play",
        "category": "mobile",
        "icon": "▶",
        "eyebrow": "Android",
        "title": "Publish an Android app on Google Play",
        "summary": "Create a signed Android App Bundle, test it through Play, complete the listing, and roll out a reviewed release.",
        "result": "An approved Play Store listing and managed app release",
        "artifact": "A release-signed .aab with a unique package name and higher versionCode",
        "time": "Account verification, testing, and review take time",
        "good_for": ("Native Android", "Flutter Android", "React Native Android", "Game-engine Android exports"),
        "not_for": "An APK or debug build copied straight from an editor. Google Play’s normal publishing flow uses an Android App Bundle.",
        "prerequisites": (
            "A verified Play Console developer account.",
            "A final package name/application ID; changing identity later creates a different app.",
            "An upload key stored securely and a release-signed Android App Bundle.",
            "Store graphics, support details, privacy policy, and truthful data-safety answers.",
        ),
        "steps": (
            {
                "title": "Create the app in Play Console",
                "body": "Choose Create app, set the default language and name, declare app/game and free/paid status, provide a contact email, accept the declarations, and create the app. Review the Dashboard tasks before planning a launch date.",
            },
            {
                "title": "Configure signing and build the bundle",
                "body": "Follow the signing workflow for your actual framework or game engine. Enroll in Play App Signing and protect the separate upload key. Generate a release .aab—not a debug build—and keep its package name consistent with the Play app record.",
            },
            {
                "title": "Complete App content and the store listing",
                "body": "In Play Console, complete privacy policy, ads, app access, target audience, content rating, data safety, and any other shown declarations. Add the descriptions, icon, feature graphic, screenshots, category, support contact, and countries/regions. Answers must match the shipped binary and backend behavior.",
            },
            {
                "title": "Upload to an internal test first",
                "body": "Create an Internal testing release, upload the .aab, name the release, add notes, resolve every error, and roll it out to testers. Install from the tester Play link—do not treat a USB-installed build as proof that Play delivery works.",
            },
            {
                "title": "Meet the testing requirement for your account",
                "body": "Use closed testing when required. Google’s current rule for personal developer accounts created after November 13, 2023 requires at least 12 testers to remain continuously opted in to a closed test for 14 days before you can apply for production access. Play Console is the authority for your account’s current eligibility, so follow the exact instructions it shows and recheck the linked official requirement page before planning a launch date.",
            },
            {
                "title": "Create and review the production release",
                "body": "When production access and listing tasks are complete, create a Production release, select or upload the tested bundle, add release notes, review warnings, and start rollout. Monitor review status, crashes, Android vitals, policy messages, and staged rollout results.",
            },
        ),
        "checklist": ("Release .aab is signed with the protected upload key", "Play-delivered test install works", "Data Safety matches actual collection and sharing", "Every required dashboard task is complete", "versionCode increases on every update"),
        "sources": (
            {"label": "Android: sign your app", "url": "https://developer.android.com/studio/publish/app-signing"},
            {"label": "Android: upload your app bundle", "url": "https://developer.android.com/studio/publish/upload-bundle"},
            {"label": "Play Console: prepare and roll out a release", "url": "https://support.google.com/googleplay/android-developer/answer/9859348"},
            {"label": "Play Console: testing requirements for new personal accounts", "url": "https://support.google.com/googleplay/android-developer/answer/14151465"},
        ),
    },
    {
        "slug": "apple-app-store",
        "category": "mobile",
        "icon": "●",
        "eyebrow": "iPhone, iPad & Apple platforms",
        "title": "Ship an app with App Store Connect",
        "summary": "Create the matching app record, sign and upload a build, test with TestFlight, then submit complete metadata for review.",
        "result": "A reviewed App Store release, with TestFlight beta testing first",
        "artifact": "A signed archive/build whose bundle ID matches App Store Connect",
        "time": "Signing, beta testing, metadata, and review take time",
        "good_for": ("iOS", "iPadOS", "watchOS companion apps", "visionOS", "tvOS", "macOS App Store"),
        "not_for": "Direct macOS downloads outside the store; use the Developer ID and notarization guide for that route.",
        "prerequisites": (
            "Active Apple Developer Program membership and App Store Connect access.",
            "An explicit App ID/bundle ID and signing configuration for the correct team.",
            "A supported Mac and current Apple build/upload tooling for the target platform.",
            "Screenshots, privacy details, support URL, age rating, and review contact information.",
        ),
        "steps": (
            {
                "title": "Register identity and create the app record",
                "body": "Register the bundle ID in Certificates, Identifiers & Profiles. In App Store Connect choose Apps → plus button → New App, then select platforms and enter the name, primary language, matching bundle ID, SKU, and user-access setting. The build and record must use the same bundle ID.",
            },
            {
                "title": "Archive a release-signed build",
                "body": "Use the release/archive workflow documented for your framework. In Xcode, select the real device or generic destination and create an archive. Resolve signing, entitlement, icon, and version/build-number errors before upload; do not upload a simulator or development build.",
            },
            {
                "title": "Upload and wait for processing",
                "body": "Upload from Xcode Organizer, Transporter, or another Apple-supported tool. App Store Connect processes the build before it can be selected. Read and fix all processing emails and warnings.",
            },
            {
                "title": "Test through TestFlight",
                "body": "Add internal testers first and install the processed build through TestFlight. Test sign-in, purchases, push notifications, permissions, account deletion, poor networks, and upgrades. External testing requires beta review and beta metadata.",
            },
            {
                "title": "Complete the version and privacy metadata",
                "body": "Add description, keywords, support and marketing URLs, screenshots for required device sizes, age rating, availability and pricing, App Privacy answers, export-compliance details, and App Review contact/demo credentials. Select the tested build for the version.",
            },
            {
                "title": "Add for Review, then submit",
                "body": "On the app-version page verify the selected build, choose Add for Review, inspect the draft submission, then choose Submit for Review. These are separate actions. Monitor messages, respond to review questions, and control manual, automatic, or phased release after approval.",
            },
        ),
        "checklist": ("Bundle ID matches the app record", "TestFlight install passes on real target devices", "Privacy labels and permission prompts are truthful", "Review account works without private assistance", "Release method is intentionally selected"),
        "sources": (
            {"label": "App Store Connect: add a new app", "url": "https://developer.apple.com/help/app-store-connect/create-an-app-record/add-a-new-app/"},
            {"label": "App Store Connect: upload builds", "url": "https://developer.apple.com/help/app-store-connect/manage-builds/upload-builds/"},
            {"label": "App Store Connect: TestFlight overview", "url": "https://developer.apple.com/help/app-store-connect/test-a-beta-version/testflight-overview/"},
            {"label": "App Store Connect: submit an app", "url": "https://developer.apple.com/help/app-store-connect/manage-submissions-to-app-review/submit-an-app/"},
        ),
    },
    {
        "slug": "itchio",
        "category": "games",
        "icon": "♟",
        "eyebrow": "Games · simple storefront",
        "title": "Release a game on itch.io",
        "summary": "Upload a browser game or downloadable builds directly, or push repeatable updates with Butler.",
        "result": "A customizable itch.io project page with playable or downloadable builds",
        "artifact": "Browser ZIP with index.html, or versioned native build directories",
        "time": "A practical first game-release route",
        "good_for": ("Game jams", "HTML5 games", "Windows/macOS/Linux downloads", "Early builds"),
        "not_for": "Uploading an editor project and expecting itch.io to export it. Export a production build for each target first.",
        "prerequisites": (
            "An itch.io creator account and a tested game export.",
            "Cover art, screenshots, description, controls, and platform labels.",
            "Rights to distribute every included asset and runtime.",
        ),
        "steps": (
            {
                "title": "Export and test the release build",
                "body": "Use your engine’s official export workflow. For a browser game, put index.html at the root of a ZIP with all required .js, .wasm, data, and asset files beside it in the paths the game expects. Test it through a local web server, not by double-clicking the file.",
            },
            {
                "title": "Create the project page as a draft",
                "body": "Choose Upload new project, enter the title and project URL, select the kind of project, add a short description and classification, and keep visibility at Draft while testing. Add clear controls and system requirements.",
            },
            {
                "title": "Upload in the dashboard",
                "body": "Upload the browser ZIP and mark it as played in the browser, or upload native archives and mark their operating systems. Configure viewport/embed options for HTML games. Open the draft page and test every upload.",
            },
            {
                "title": "Use Butler for repeatable updates (optional)",
                "body": "Install Butler from itch.io’s official instructions. Push a build directory to a named channel such as windows, linux, mac, or html5. Replace the placeholders; the channel tells the itch app which build users need.",
                "commands": (
                    {"label": "Push a build directory", "text": "butler push <BUILD_DIRECTORY> <ITCH_USER>/<GAME>:<CHANNEL>", "replace": "Replace build directory, user, game, and channel."},
                    {"label": "Push with your own version", "text": "butler push <BUILD_DIRECTORY> <ITCH_USER>/<GAME>:<CHANNEL> --userversion <VERSION>", "replace": "Replace every placeholder."},
                ),
            },
            {
                "title": "Set access, pricing, and publish",
                "body": "Choose Draft, Restricted, or Public deliberately; set pricing and payments only after reading itch.io’s seller documentation. Preview the page logged out, test the browser frame or downloads, then change visibility to Public when support information and builds are ready.",
            },
        ),
        "checklist": ("Browser ZIP has index.html at its root", "Each native upload has correct platform labels", "Draft page and downloads were tested logged out", "Controls, support, and system requirements are visible"),
        "sources": (
            {"label": "itch.io: Uploading HTML5 games", "url": "https://itch.io/docs/creators/html5"},
            {"label": "itch.io Butler: pushing builds", "url": "https://itch.io/docs/butler/pushing.html"},
            {"label": "Godot: exporting for the Web (engine example)", "url": "https://docs.godotengine.org/en/stable/tutorials/export/exporting_for_web.html"},
        ),
    },
    {
        "slug": "steam",
        "category": "games",
        "icon": "◉",
        "eyebrow": "Games · PC storefront",
        "title": "Prepare and release a game on Steam",
        "summary": "Complete Steamworks onboarding, build the store page and depots, pass review, and use release controls.",
        "result": "A reviewed Steam store page and downloadable game build",
        "artifact": "Tested PC builds arranged into SteamPipe depots plus store assets",
        "time": "Plan weeks, not hours, for a first release",
        "good_for": ("Commercial PC games", "Demos", "Windows/macOS/Linux builds", "Steam features"),
        "not_for": "A same-day upload. Steam’s first releases have onboarding, fee, waiting, Coming Soon, and review requirements.",
        "prerequisites": (
            "Legal name/entity, bank, tax, identity, and payment information for Steamworks onboarding.",
            "The per-product Steam Direct fee shown during onboarding.",
            "Release builds, supported-OS test machines, store art, trailer/screenshots, description, ratings disclosures, and support details.",
        ),
        "steps": (
            {
                "title": "Complete Steamworks onboarding",
                "body": "Sign the digital agreements, verify identity, complete tax and bank information, and pay the product submission fee. Valve’s official onboarding page currently states US$100 or local equivalent per product; verify the live amount and rules there before paying.",
            },
            {
                "title": "Plan around the first-release timing rules",
                "body": "Steam currently requires a 30-day wait after paying the app fee before release and a publicly visible Coming Soon page for at least two weeks for first titles. These timers can overlap, but confirm your own dashboard and the official onboarding page before announcing a date.",
            },
            {
                "title": "Complete the store presence",
                "body": "Fill every required store-page section: written description, supported platforms and languages, features, system requirements, content survey, legal/support links, screenshots, trailers, capsule/library art, pricing request, and release date. Use only assets that meet Steam’s current specifications.",
            },
            {
                "title": "Configure depots and upload with SteamPipe",
                "body": "Define which files belong in each depot and which packages grant them. Follow SteamPipe’s current SDK and Build Scripts documentation to create the app-build and depot-build configuration for your actual App ID and paths. Do not copy somebody else’s VDF identifiers. Upload a build, place it on a non-default branch, and install it through Steam for testing.",
            },
            {
                "title": "Test the Steam-delivered build",
                "body": "Test clean installs, updates, uninstall/reinstall, saves, controllers, overlays, achievements/cloud features you advertise, redistributables, and every supported OS. Confirm the executable and launch options work without your development tools installed.",
            },
            {
                "title": "Submit store and build for review",
                "body": "Use the Steamworks readiness checklists and submit the store presence and build before your planned date. Valve reviews the page and runs the build; the onboarding documentation says this review commonly takes 1–5 days, but leave contingency time and resolve feedback before release.",
            },
            {
                "title": "Release intentionally",
                "body": "After approval and all timing requirements, use the release controls in Steamworks, verify the live store and install, monitor discussions and crash/support reports, and use SteamPipe branches for tested updates before making a build default.",
            },
        ),
        "checklist": ("Onboarding and product fee are complete", "Coming Soon and waiting periods are satisfied", "Steam-installed build passes on supported OSes", "Store and build reviews are approved", "Support and rollback plan are ready"),
        "sources": (
            {"label": "Steamworks: onboarding", "url": "https://partner.steamgames.com/doc/gettingstarted/onboarding"},
            {"label": "Steamworks: builds and SteamPipe", "url": "https://partner.steamgames.com/doc/store/application/builds"},
            {"label": "Steamworks: release process", "url": "https://partner.steamgames.com/doc/store/releasing"},
        ),
    },
    {
        "slug": "microsoft-store",
        "category": "desktop",
        "icon": "⊞",
        "eyebrow": "Windows",
        "title": "Submit a Windows app to Microsoft Store",
        "summary": "Reserve the product, package and test it, complete the Partner Center submission, then pass certification.",
        "result": "A certified Microsoft Store product listing",
        "artifact": "A Store package such as .msixupload, or a compliant MSI/EXE installer",
        "time": "Packaging plus store certification",
        "good_for": ("Windows desktop apps", "Packaged PWAs", "MSIX", "Supported MSI/EXE installers"),
        "not_for": "A raw source-code ZIP. The Store needs a supported, installable package or installer and complete submission metadata.",
        "prerequisites": (
            "A verified Partner Center developer account.",
            "A reserved product name and package identity that match the submission.",
            "A clean-machine-tested package or installer; unpackaged MSI/EXE submissions must also meet Microsoft’s signing, hosting, offline, and silent-install requirements.",
            "Description, logos/screenshots, privacy and support links, age-rating answers, pricing and market choices.",
        ),
        "steps": (
            {
                "title": "Reserve the product",
                "body": "In Partner Center create a new app, reserve its name, and note the assigned Store identity. If packaging with Visual Studio or another framework tool, associate the project with that Store product so identity fields match.",
            },
            {
                "title": "Choose and create a supported package",
                "body": "For MSIX, create an app package for Microsoft Store submission and prefer the .msixupload output because it contains the packages and symbol data used by the Store. Microsoft also supports eligible MSI or EXE apps through a separate unpackaged-app path; follow that path’s current requirements instead of renaming an installer.",
            },
            {
                "title": "Prepare an unpackaged installer when using MSI or EXE",
                "body": "Digitally sign the installer with a certificate from a Certificate Authority in Microsoft’s Trusted Root Program. It must be a standalone/offline installer, support silent unattended installation, and be available from a secure versioned HTTPS URL that Partner Center can download. Keep that exact installer available after submission; for an update, increment the version and provide a new installer URL. Recheck Microsoft’s package-requirements page because this path has additional installer behavior and malware-scan rules.",
            },
            {
                "title": "Test installation and upgrades",
                "body": "Install the exact submitted package or hosted installer on a clean supported Windows machine. Test launch, silent install when using MSI/EXE, uninstall, upgrade from the previous version, file/protocol associations, permissions, offline/error behavior, and every declared architecture. Run the Windows App Certification Kit when it applies to your package.",
            },
            {
                "title": "Complete the Partner Center submission",
                "body": "Fill Pricing and availability, Properties, Age ratings/IARC, Packages, Store listings, and Submission options. Upload the real package, resolve validation errors, provide accurate notes for certification, and include login credentials if reviewers need them.",
            },
            {
                "title": "Submit and monitor certification",
                "body": "Review the summary, submit for certification, monitor status and reports, and fix failures in a new package rather than masking them in metadata. After publication, install from the Store listing and verify licensing, updates, and support links.",
            },
        ),
        "checklist": ("Package identity matches Partner Center", "Clean install and upgrade pass", "MSI/EXE signing, HTTPS URL, offline, and silent-install rules pass", "Architectures and capabilities are accurate", "Certification notes include working test access", "Store-delivered install works"),
        "sources": (
            {"label": "Microsoft Learn: get started publishing Windows apps", "url": "https://learn.microsoft.com/en-us/windows/apps/publish/get-started"},
            {"label": "Microsoft Learn: package a desktop or UWP app", "url": "https://learn.microsoft.com/en-us/windows/msix/package/packaging-uwp-apps"},
            {"label": "Microsoft Learn: MSI/EXE package requirements", "url": "https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/msi/app-package-requirements"},
            {"label": "Microsoft Learn: upload MSI/EXE packages", "url": "https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/msi/upload-app-packages"},
        ),
    },
    {
        "slug": "macos-direct",
        "category": "desktop",
        "icon": "⌘",
        "eyebrow": "macOS outside the store",
        "title": "Sign and notarize a direct macOS download",
        "summary": "Use Developer ID signing and Apple notarization before distributing a .dmg, .pkg, or archive from your own site.",
        "result": "A signed and notarized download that Gatekeeper can validate",
        "artifact": "Developer ID-signed app inside a supported distribution container",
        "time": "Signing and notarization before hosting",
        "good_for": ("Direct Mac downloads", "Apps outside Mac App Store", "Developer-hosted updates"),
        "not_for": "iPhone/iPad distribution or a Mac App Store listing. Those use App Store Connect submission and review.",
        "prerequisites": (
            "Apple Developer Program membership and the correct team access.",
            "A Developer ID Application certificate; installers may also need Developer ID Installer.",
            "A release archive with hardened runtime and correct entitlements.",
            "A secure download host and update/signing strategy.",
        ),
        "steps": (
            {
                "title": "Create and protect Developer ID certificates",
                "body": "In Certificates, Identifiers & Profiles create the Developer ID certificate type required by your distribution method. Install it with its private key only on trusted build machines or a secured signing service. Do not export the private key into the app or repository.",
            },
            {
                "title": "Archive and sign every nested component",
                "body": "Build a Release archive with hardened runtime and only the entitlements the app uses. Frameworks, helpers, plug-ins, XPC services, and bundled command-line tools must be signed correctly; signing only the outer .app does not repair invalid nested code.",
            },
            {
                "title": "Package without breaking the signature",
                "body": "Export using the Developer ID distribution workflow and create the .dmg, .pkg, or ZIP using tooling supported by Apple’s notarization workflow. Verify the app launches on a clean Mac before submission.",
            },
            {
                "title": "Submit to Apple’s notary service",
                "body": "Use Xcode’s notarization flow or Apple’s current notarytool workflow. Wait for the result and read the notarization log. A successful upload is not the same as an Accepted result; fix signing, entitlement, or malware-scan failures and resubmit the rebuilt artifact.",
            },
            {
                "title": "Staple, validate, and test offline",
                "body": "Staple the notarization ticket to the distributable where supported, validate it, then download it through the same HTTPS path users will use. Test first launch on a clean Mac with Gatekeeper enabled, including a temporarily offline test so the stapled ticket can be checked.",
            },
            {
                "title": "Publish with integrity and support details",
                "body": "Host the exact notarized artifact over HTTPS. Publish its version, system requirements, checksum, privacy/support links, and update instructions. Sign updates with the expected identity and notarize each new release.",
            },
        ),
        "checklist": ("Developer ID signature validates", "Notary result is Accepted", "Ticket is stapled where supported", "Downloaded file passes Gatekeeper on a clean Mac", "Every update repeats signing and notarization"),
        "sources": (
            {"label": "Apple: create Developer ID certificates", "url": "https://developer.apple.com/help/account/certificates/create-developer-id-certificates/"},
            {"label": "Apple: notarizing macOS software", "url": "https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution"},
            {"label": "Apple: customizing the notarization workflow", "url": "https://developer.apple.com/documentation/security/customizing-the-notarization-workflow"},
        ),
    },
    {
        "slug": "flathub",
        "category": "desktop",
        "icon": "▱",
        "eyebrow": "Linux",
        "title": "Submit a Flatpak to Flathub",
        "summary": "Build and lint a Flatpak manifest locally, then send the required files through Flathub’s new-app pull-request process.",
        "result": "A reviewed Flathub app with its own maintained repository",
        "artifact": "A valid Flatpak manifest plus required metadata and icons",
        "time": "Technical packaging plus volunteer review",
        "good_for": ("Linux desktop apps", "Sandboxed distribution", "Cross-distro installs"),
        "not_for": "A binary upload alone. Flathub builds from a manifest and reviews the packaging repository.",
        "prerequisites": (
            "Basic Git and Flatpak familiarity.",
            "A stable reverse-DNS app ID and all files required by Flathub policy.",
            "Source/download URLs that the Flathub builders can access.",
            "A GitHub account with two-factor authentication for maintainer access after approval.",
        ),
        "steps": (
            {
                "title": "Read requirements before packaging",
                "body": "Check Flathub’s app requirements, metadata quality guidance, licensing, network-source, sandbox, and generative-AI policies. Pick the final app ID before filenames, desktop metadata, AppStream metadata, icons, and manifest references are created.",
            },
            {
                "title": "Install the recommended builder",
                "body": "Flathub recommends its org.flatpak.Builder app. Add Flathub for the current user, then install the builder exactly as documented.",
                "commands": (
                    {"label": "Add Flathub for this user", "text": "flatpak remote-add --if-not-exists --user flathub https://dl.flathub.org/repo/flathub.flatpakrepo"},
                    {"label": "Install the builder", "text": "flatpak install -y flathub org.flatpak.Builder"},
                ),
            },
            {
                "title": "Build, install, and run the manifest",
                "body": "Replace the placeholders with the manifest path and final app ID. Fix build and sandbox failures locally before opening a pull request.",
                "commands": (
                    {"label": "Build and install", "text": "flatpak run --command=flathub-build org.flatpak.Builder --install <MANIFEST>", "replace": "Replace <MANIFEST>."},
                    {"label": "Run the installed app", "text": "flatpak run <APP_ID>", "replace": "Replace <APP_ID>."},
                ),
            },
            {
                "title": "Run both linters",
                "body": "Run the manifest linter and repository linter using Flathub’s documented commands. Resolve errors or document a justified exception under current policy.",
                "commands": (
                    {"label": "Lint the manifest", "text": "flatpak run --command=flatpak-builder-lint org.flatpak.Builder manifest <MANIFEST>", "replace": "Replace <MANIFEST>."},
                    {"label": "Lint the repository", "text": "flatpak run --command=flatpak-builder-lint org.flatpak.Builder repo repo"},
                ),
            },
            {
                "title": "Open the submission PR against new-pr",
                "body": "Fork flathub/flathub without limiting the fork to master, create a branch from the new-pr base, add only the required files, and open a GitHub pull request whose base branch is new-pr—not master. Use the title format requested by Flathub.",
            },
            {
                "title": "Respond to review and test the bot build",
                "body": "Keep the pull request open while addressing comments. After reviewers allow it, request the test build using the bot instruction in the official submission guide and test that build. On approval, accept the maintainer invitation promptly and follow Flathub’s maintenance process for updates.",
            },
        ),
        "checklist": ("Local Flatpak build and run pass", "Manifest and repo linters pass", "PR targets new-pr", "Metadata and screenshots match the app", "GitHub 2FA is enabled for maintainer access"),
        "sources": (
            {"label": "Flathub: submission process", "url": "https://docs.flathub.org/docs/for-app-authors/submission"},
            {"label": "Flathub: app requirements", "url": "https://docs.flathub.org/docs/for-app-authors/requirements"},
            {"label": "Flathub: app maintenance", "url": "https://docs.flathub.org/docs/for-app-authors/maintenance"},
        ),
    },
    {
        "slug": "chrome-web-store",
        "category": "distribution",
        "icon": "◌",
        "eyebrow": "Browser extension",
        "title": "Publish a Chrome extension",
        "summary": "Package the extension as a ZIP, complete the Developer Dashboard listing and privacy fields, then submit it for review.",
        "result": "A reviewed Chrome Web Store listing or controlled distribution",
        "artifact": "A ZIP with manifest.json at its root and only production extension files",
        "time": "Developer registration, policy work, and review",
        "good_for": ("Chrome extensions", "Chromium browser tools", "Public or controlled listings"),
        "not_for": "A normal website or PWA. The Chrome Web Store accepts extensions with an extension manifest, not arbitrary web-app hosting.",
        "prerequisites": (
            "A Chrome Web Store developer account and paid registration where required.",
            "A locally loaded and tested extension with the narrowest permissions it needs.",
            "Store icon/screenshots, description, support details, privacy disclosures, and any required privacy policy.",
        ),
        "steps": (
            {
                "title": "Create the production ZIP",
                "body": "Put manifest.json at the ZIP root with production scripts, pages, icons, and assets. Exclude source-only files, tests, local secrets, private keys, node_modules, and unrelated archives. Increment the manifest version for every update.",
            },
            {
                "title": "Test the exact unpacked extension",
                "body": "Load the production directory from chrome://extensions in Developer mode. Test install/update, every declared permission, restricted and normal sites, browser restarts, sign-out, offline/error behavior, and removal. Remove permissions you cannot justify.",
            },
            {
                "title": "Upload in the Developer Dashboard",
                "body": "In the Chrome Developer Dashboard choose Add new item and upload the ZIP. Fix package validation errors before writing the final listing; do not work around them by hiding required code or behavior.",
            },
            {
                "title": "Complete listing, privacy, and distribution",
                "body": "Fill the Store Listing, Privacy, Distribution, and Test instructions tabs. Explain the extension’s single purpose, justify permissions and remote-data use, choose countries and visibility, provide review credentials or setup steps, and certify disclosures truthfully.",
            },
            {
                "title": "Submit and choose publishing behavior",
                "body": "Submit for review. If you need a coordinated launch, choose deferred publishing so approval does not immediately make the item live. Monitor dashboard and email for review questions; answer with reproducible steps and update the package when code must change.",
            },
            {
                "title": "Verify and maintain the listing",
                "body": "Install from the final Web Store listing and test again. Monitor policy notices and user reports. Upload future versions with a higher manifest version and recheck privacy disclosures whenever permissions, data use, or remote services change.",
            },
        ),
        "checklist": ("manifest.json is at ZIP root", "Requested permissions are minimal and justified", "Privacy disclosures match code and backend", "Reviewer can reach every gated feature", "Deferred/immediate publishing choice is correct"),
        "sources": (
            {"label": "Chrome for Developers: publish in the Web Store", "url": "https://developer.chrome.com/docs/webstore/publish"},
            {"label": "Chrome Web Store: program policies", "url": "https://developer.chrome.com/docs/webstore/program-policies"},
        ),
    },
)

GUIDES_BY_SLUG = {guide["slug"]: guide for guide in LAUNCH_GUIDES}

ARTIFACT_ROUTES = (
    {"value": "static", "label": "A folder with index.html", "guides": ("cloudflare-pages",), "note": "Static site or browser export"},
    {"value": "frontend", "label": "A frontend/framework repository", "guides": ("vercel-web", "cloudflare-pages"), "note": "Build on a web platform"},
    {"value": "server", "label": "An API/server app or app with a database", "guides": ("render-web-service", "docker-hub"), "note": "Needs a running service"},
    {"value": "pwa", "label": "A hosted site plus web manifest", "guides": ("installable-pwa",), "note": "Add installability after hosting"},
    {"value": "aab", "label": "A signed Android .aab", "guides": ("google-play",), "note": "Google Play"},
    {"value": "apple", "label": "An Xcode archive / Apple build", "guides": ("apple-app-store", "macos-direct"), "note": "Choose store or direct Mac distribution"},
    {"value": "webgame", "label": "A browser game export", "guides": ("itchio", "cloudflare-pages"), "note": "Playable page or game storefront"},
    {"value": "pcgame", "label": "A native PC game build", "guides": ("itchio", "steam"), "note": "Direct storefront or Steam"},
    {"value": "windows", "label": "A Windows .msix, MSI, or EXE", "guides": ("microsoft-store",), "note": "Microsoft Store"},
    {"value": "flatpak", "label": "A Flatpak manifest", "guides": ("flathub",), "note": "Flathub"},
    {"value": "extension", "label": "A browser extension ZIP", "guides": ("chrome-web-store",), "note": "Chrome Web Store"},
    {"value": "container", "label": "A Dockerfile or container image", "guides": ("docker-hub", "render-web-service"), "note": "Registry plus a runtime host"},
)


def guides_for_category(category):
    """Return guides for a known category; unknown input safely falls back to all."""
    valid = {item["slug"] for item in CATEGORIES}
    if category not in valid or category == "all":
        return LAUNCH_GUIDES, "all"
    return tuple(guide for guide in LAUNCH_GUIDES if guide["category"] == category), category
