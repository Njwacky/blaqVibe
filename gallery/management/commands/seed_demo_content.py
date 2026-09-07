"""Seed an honest, clearly labelled showcase catalogue.

This command is deliberately separate from the older local fixture command. It
creates no credentials anyone can use and never writes engagement rows.
"""
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from gallery.models import AppProject, Category, Challenge, Tag
from gallery.skill_models import Skill
from users.models import Profile

DEMO_BIO = "Demo profile created by BlaqVibes to showcase what creators can build on the platform."
DEMO_PREFIX = "demo_"

CREATORS = [
    ("demo_studio", "BlaqVibes Demo Studio", "Creative Studio", "Durban, South Africa", "violet"),
    ("demo_ubuntu_lab", "Ubuntu Design Lab", "Graphic Design", "Johannesburg, South Africa", "gold"),
    ("demo_coastal_visuals", "Coastal Visuals Demo", "Photography", "Durban, South Africa", "cyan"),
    ("demo_africode_lab", "AfriCode Creative Lab", "Web Development", "Cape Town, South Africa", "emerald"),
    ("demo_rhythm_visuals", "Rhythm & Visuals Demo", "Music and Media", "KwaZulu-Natal, South Africa", "crimson"),
]

PROJECTS = [
    ("demo-african-startup-landing", "African Startup Landing Page", "A warm, confident landing page concept for an African technology venture.", "demo_africode_lab", "landing-pages", "Web / Technology", "HTML, CSS, JavaScript"),
    ("demo-durban-events", "Durban Events Website", "A bright events discovery experience inspired by Durban's creative calendar and coastline.", "demo_studio", "landing-pages", "Web / Technology", "HTML, CSS, JavaScript"),
    ("demo-africode-portfolio", "AfriCode Portfolio", "A focused portfolio layout showing how a developer can tell a clear project story.", "demo_africode_lab", "full-apps", "Web / Technology", "HTML, CSS"),
    ("demo-creative-agency-dashboard", "Creative Agency Dashboard", "A calm project dashboard concept for tracking briefs, milestones and client handoffs.", "demo_studio", "dashboard", "Business / Technology", "HTML, CSS, JavaScript"),
    ("demo-ubuntu-brand-identity", "Ubuntu Brand Identity", "A visual identity system exploring community, rhythm and confident African typography.", "demo_ubuntu_lab", "landing-pages", "Design", "HTML, CSS"),
    ("demo-streetwear-campaign", "African Streetwear Campaign", "An editorial campaign page for a fictional streetwear drop rooted in local texture and movement.", "demo_ubuntu_lab", "landing-pages", "Design", "HTML, CSS"),
    ("demo-kzn-coffee-brand", "KZN Coffee Brand", "A considered packaging and digital launch concept for a fictional KwaZulu-Natal coffee label.", "demo_ubuntu_lab", "landing-pages", "Design / Business", "HTML, CSS"),
    ("demo-festival-posters", "Music Festival Poster Collection", "A responsive poster wall showing how one event identity can flex across a series.", "demo_rhythm_visuals", "landing-pages", "Design / Media", "HTML, CSS"),
    ("demo-durban-golden-hour", "Durban Golden Hour", "A visual exploration of Durban's coastline, architecture and creative culture, created as a demonstration of how photographers can showcase their work on BlaqVibes.", "demo_coastal_visuals", "full-apps", "Photography", "HTML, CSS"),
    ("demo-african-city-portraits", "African City Portraits", "A gallery concept pairing short field notes with portraits of city life, light and movement.", "demo_coastal_visuals", "full-apps", "Photography", "HTML, CSS, JavaScript"),
    ("demo-kzn-coastline", "KZN Coastline Collection", "A quiet, image-led collection celebrating beaches, rock pools and the changing Indian Ocean horizon.", "demo_coastal_visuals", "full-apps", "Photography", "HTML, CSS"),
    ("demo-afrobeat-visual-concept", "Afrobeat Visual Concept", "A motion-inspired cover concept translating rhythm, colour and late-night energy into a digital canvas.", "demo_rhythm_visuals", "landing-pages", "Music / Media", "HTML, CSS, JavaScript"),
    ("demo-local-brand-kit", "Small Business Brand Kit", "A practical brand kit concept showing how a local business can move from mark to social launch.", "demo_studio", "landing-pages", "Business / Design", "HTML, CSS"),
    ("demo-restaurant-makeover", "Local Restaurant Digital Makeover", "A welcoming menu and booking experience for a fictional neighbourhood restaurant.", "demo_studio", "landing-pages", "Business / Web", "HTML, CSS, JavaScript"),
]

SKILLS = [
    ("demo-logo-brand-identity", "Logo and Brand Identity Design", "Build a coherent identity that works from a small avatar to a full campaign.", "Turn a rough business idea into a visual system that feels intentional.", "Clarify the audience and promise; collect visual references; sketch three directions; test the strongest mark at small sizes; document type, colour and usage rules.", "Figma, Illustrator", "intermediate", "A compact brand board with logo, type, colours and usage notes."),
    ("demo-landing-page-story", "Landing Page Storytelling", "Shape a landing page around one clear audience promise and next step.", "Avoid beautiful pages that leave visitors unsure what to do.", "Write the one-sentence promise; order proof and benefits; draft a responsive hierarchy; make the primary action obvious; test the page at mobile width.", "HTML, CSS, accessibility", "beginner", "A responsive landing page with a clear call to action."),
    ("demo-coastal-photo-edit", "Coastal Photography Edit", "Create a consistent photo set from changing natural light and weather.", "Turn a mixed shoot into a collection with a recognisable visual rhythm.", "Select a story; establish a crop and colour direction; sequence wide, medium and detail frames; write concise captions; export web-sized images.", "Lightroom, Capture One", "intermediate", "A five-to-eight-image web gallery with captions."),
    ("demo-social-campaign", "Social Media Design System", "Make a campaign feel like one idea across multiple social formats.", "Keep momentum without designing every post from scratch.", "Define the campaign line; create a type and spacing kit; design one hero post; adapt it to square, story and reel cover formats; check contrast.", "Figma, Canva", "beginner", "A reusable set of four campaign templates."),
    ("demo-ui-ux-brief", "UI and UX Brief Mapping", "Translate a vague product idea into a small, testable interface brief.", "Give a creative team shared language before pixels take over.", "Name the user and task; map the happy path; list constraints; sketch the first screen; identify one assumption to test with a real person.", "FigJam, Figma", "beginner", "A one-page brief and low-fidelity flow."),
    ("demo-music-visualiser", "Music Visualiser Direction", "Plan a visual language that responds to music without overwhelming the track.", "Connect sound, movement and identity into a coherent visual concept.", "Listen for structure; assign colour and shape to sections; storyboard three transitions; choose a restrained motion rule; prepare a short loop for review.", "After Effects, Blender", "advanced", "A storyboard and fifteen-second visual loop."),
    ("demo-local-business-web", "Local Business Web Refresh", "Design a useful, welcoming web presence for a neighbourhood business.", "Make opening hours, menu, location and contact details effortless to find.", "Interview the business owner; audit the current information; prioritise mobile tasks; build the page structure; check copy, links and load weight.", "HTML, CSS, content design", "intermediate", "A mobile-first homepage prototype and content checklist."),
]

CHALLENGES = [
    ("demo-challenge-durban-60", "Durban in 60 Seconds", "Create a short visual story that captures Durban's energy, culture or coastline.", "durban-in-60-seconds"),
    ("demo-challenge-african-future", "African Future", "Imagine what African cities, technology and creativity could look like in the future.", "african-future"),
    ("demo-challenge-creative-identity", "My Creative Identity", "Show the world what makes your creative style unique.", "my-creative-identity"),
    ("demo-challenge-local-brand", "Local Brand Challenge", "Create a brand concept inspired by a local business or community.", "local-brand-challenge"),
]


def demo_html(title, description, stack):
    return f'''<main style="font-family:system-ui;max-width:760px;margin:0 auto;padding:3rem;background:#10101a;color:#f8fafc;min-height:100vh"><p style="color:#a78bfa;letter-spacing:.12em;text-transform:uppercase;font-size:.75rem">BlaqVibes demo project</p><h1>{title}</h1><p style="color:#cbd5e1;line-height:1.7">{description}</p><div style="margin-top:2rem;padding:1rem;border:1px solid #3f3f5a;border-radius:12px">Explore the concept, read the story, and imagine your own version.</div><small style="display:block;margin-top:2rem;color:#94a3b8">Showcase sample · {stack}</small></main>'''


class Command(BaseCommand):
    help = "Create clearly labelled BlaqVibes showcase content without fake engagement."

    @transaction.atomic
    def handle(self, *args, **options):
        users = {}
        created_users = created_projects = created_skills = created_challenges = 0
        for username, display_name, category, location, colour in CREATORS:
            user, made = User.objects.get_or_create(
                username=username,
                defaults={"email": f"{username}@demo.blaqvibes.local"},
            )
            if made:
                user.set_unusable_password()
                user.save(update_fields=["password"])
                created_users += 1
            profile, _ = Profile.objects.get_or_create(user=user)
            # A collision with a real account is left completely untouched.
            # The stable username is our identifier only after the demo bio
            # proves that this command owns the record.
            if not made and profile.bio != DEMO_BIO:
                self.stdout.write(self.style.WARNING(
                    f"Skipping demo username collision: {username}"
                ))
                continue
            profile.bio = DEMO_BIO
            profile.location = location
            profile.name_color = colour
            profile.save(update_fields=["bio", "location", "name_color"])
            users[username] = user

        categories = {}
        for slug, name, kind in [("landing-pages", "Landing Pages", "snippet"), ("dashboard", "Dashboards", "snippet"), ("full-apps", "Full Apps", "full_app")]:
            categories[slug], _ = Category.objects.get_or_create(slug=slug, defaults={"name": name, "type": kind})

        for slug, title, description, owner_name, category, label, stack in PROJECTS:
            if owner_name not in users or AppProject.objects.filter(slug=slug).exists():
                continue
            project = AppProject.objects.create(
                owner=users[owner_name], title=title, slug=slug,
                category=categories[category], short_description=description,
                readme=f"# {title}\n\n{description}\n\nThis is clearly labelled BlaqVibes showcase content. It demonstrates how a creator can present a polished project, explain its choices and invite visitors to build their own version.\n\n## Focus\n{label}\n\n## Tools\n{stack}",
                html_code=demo_html(title, description, stack), css_code="", js_code="",
                tech_stack=stack, status="published", is_featured=True,
                preview_mode="snippet", kind="snippet", kind_source="creator",
                trust="unknown", appeal_score=0,
            )
            tag, _ = Tag.objects.get_or_create(slug=slug, defaults={"name": "Demo Showcase"})
            project.tags.add(tag)
            created_projects += 1

        for slug, title, summary, problem, workflow, tools, difficulty, expected in SKILLS:
            if "demo_studio" not in users or Skill.objects.filter(slug=slug).exists():
                continue
            Skill.objects.create(
                creator=users["demo_studio"], title=title, slug=slug,
                summary=summary, problem=problem, workflow=workflow,
                tools=tools, difficulty=difficulty, expected_output=expected,
                tags="demo, showcase, creative practice", is_published=True,
            )
            created_skills += 1

        now = timezone.now()
        for slug, title, description, tag in CHALLENGES:
            if "demo_studio" not in users or Challenge.objects.filter(tag=tag).exists():
                continue
            Challenge.objects.create(
                title=title, description=f"{description} This is a BlaqVibes demo challenge: use it as a prompt for your own work.",
                bounty_stars=0, tag=tag, start=now - timedelta(days=1),
                end=now + timedelta(days=365), is_active=True, created_by=users["demo_studio"],
            )
            created_challenges += 1

        self.stdout.write(self.style.SUCCESS(
            f"Demo showcase ready: {created_users} users, {created_projects} projects, "
            f"{created_skills} skills, {created_challenges} challenges created. No engagement seeded."
        ))
