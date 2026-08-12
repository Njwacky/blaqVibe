# BlaqVibes — README + File Tree + Comments Spec
### "stock_app_vibes" — How People Read, Explore & Comment Before They Clone

**Requirement:** Every app **MUST** have a `README.md` (what it is, stack, setup, AI prompt). People must see the **file tree** (like GitHub) + **README rendered** + **comments** before they download/clone. This is what builds trust for unpublished apps.

---

## 1. FLOW — UPLOADER MUST PROVIDE MD

### Upload Form (`/publish/`) — Enforced

**Field 1: ZIP Upload** -> If ZIP already contains `README.md` (case-insensitive, at root or `/docs/`), we auto-extract it and pre-fill the form.

**Field 2: README Editor (Required)**
- Textarea with Markdown + Live Preview (split pane)
- Validation: `len(readme.strip()) < 100` -> error "README too short — explain what stock_app_vibes does, stack, how to run"
- Template injected if empty:
```markdown
# Stock App Vibes
> One-line what it does (e.g. AI stock portfolio tracker)

## What is this?
... 2-3 sentences

## Tech Stack
- Django
- React + Tailwind
- TwelveData API

## How to Run
```bash
pip install -r requirements.txt
python manage.py runserver
```

## AI Prompt (if AI-generated)
```

## File Tree Preview
We generate instantly after ZIP drop (client-side via JS zip.js, or server after upload).
```

**Backend Enforcement (`forms.py`):**
```python
class AppUploadForm(forms.ModelForm):
    readme = forms.CharField(widget=forms.Textarea, required=True, min_length=100)
    def clean_readme(self):
        md = self.cleaned_data['readme']
        if len(md) < 100: raise ValidationError("README must be at least 100 chars.")
        if '# ' not in md: raise ValidationError("README needs at least one heading (#).")
        return md
    def clean(self):
        # If ZIP has README.md, ensure form README matches or warn
        ...
```

**If no README in ZIP:** We **create** `README.md` at root of the stored ZIP using the form content (so git clone always has it).

---

## 2. FILE TREE — "stock_app_vibes" EXAMPLE

When someone uploads `stock_app_vibes.zip` with this inside:
```
stock_app_vibes/
├── README.md
├── manage.py
├── requirements.txt
├── .env.example
├── stock_app/
│   ├── views.py
│   └── models.py
├── frontend/
│   ├── src/
│   │   ├── App.jsx
│   │   └── components/StockTable.jsx
│   └── package.json
└── screenshots/
    └── dashboard.png
```

**What users see on `/app/stock-app-vibes/` — Two tabs:**

**Tab 1: README (Default)** — Rendered Markdown (sanitized via `nh3`), same as GitHub.

**Tab 2: Files — Tree View** — Interactive, expand/collapse, like GitHub.
- **Backend:** On Celery `process_upload`, we unzip to temp, walk files, save to `AppFile` model + build `file_tree.json`:
```python
# gallery/utils.py
def build_tree(file_list):
    # file_list = ['README.md', 'stock_app/views.py', 'frontend/src/App.jsx']
    tree = {}
    for path in file_list:
        parts = path.split('/')
        node = tree
        for part in parts:
            node = node.setdefault(part, {})
    return tree
# Save to AppProject.file_tree (JSONField)
# AppFile objects for search: AppFile.objects.filter(project=..., path__icontains='views.py')
```
- **Frontend (Alpine + Tailwind) — Collapsible Tree:**
```html
<div x-data="{ open: {'stock_app': true, 'frontend': false} }">
  <ul class="text-[13px] font-mono">
    <li @click="open.stock_app = !open.stock_app" class="cursor-pointer">📁 stock_app/ <span x-text="open.stock_app ? '▾' : '▸'"></span>
      <ul x-show="open.stock_app" class="ml-4 border-l border-[#232326] pl-3">
        <li @click="viewFile('stock_app/views.py')" class="hover:text-white cursor-pointer">📄 views.py <span class="text-[#6A6A7A]">— 2.4 KB</span></li>
        <li>📄 models.py</li>
      </ul>
    </li>
    <li>📁 frontend/</li>
    <li>📄 README.md</li>
  </ul>
</div>
```
- **Click file:** Opens a **File Preview Modal/Pane** — fetches `/app/<slug>/file/<path>` (reads from S3 ZIP, escapes, highlights with Prism). No execution. Max 200KB preview, larger shows "Too large to preview — Download ZIP".

**Access Rule:** Tree + README are **public** (no login needed). Download/clone requires optional login for tracking, but viewing tree/comments is open — this is what makes people trust `stock_app_vibes` before they run it.

---

## 3. COMMENTS — PER APP, LIKE GITHUB ISSUES

**Model (`gallery/models.py`):**
```python
class Comment(models.Model):
    project = models.ForeignKey(AppProject, on_delete=models.CASCADE, related_name='comments')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.CASCADE, related_name='replies') # 1-level nesting
    body = models.TextField(max_length=2000) # Markdown, sanitized
    body_html = models.TextField(blank=True) # rendered + sanitized via nh3
    is_edited = models.BooleanField(default=False)
    is_hidden = models.BooleanField(default=False) # moderation
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['created_at']
```

**Features:**
- **Markdown in comments** too (bold, code, links) — sanitized same as README (`nh3`)
- **1-level Threading:** Reply to a comment (like GitHub), not infinite nesting — keeps UI clean
- **Sort:** Newest first, with `?sort=top` (most replies) later
- **Auth:** Must be logged in to comment. Rate limit: `5 comments/hour` via `django-ratelimit`
- **Moderation:** `is_hidden` toggle in admin, auto-hide if contains banned words (simple blocklist), report button (`Report comment` -> `CommentReport` model)
- **Notifications:** `post_save` signal -> email to `project.owner` "New comment on stock_app_vibes"

**View:**
```python
@login_required
@ratelimit(key='user', rate='5/h', method='POST')
def post_comment(request, slug):
    project = get_object_or_404(AppProject, slug=slug)
    body = request.POST.get('body','').strip()
    if len(body) < 5: return JsonResponse({'error':'Too short'}, status=400)
    html = render_markdown(body) # markdown + nh3
    Comment.objects.create(project=project, user=request.user, body=body, body_html=html)
    return redirect(project.get_absolute_url() + '#comments')
```

**Frontend (`app_detail.html` — below README/Tree):**
```html
<section id="comments" class="mt-6 bg-[#121214] border border-[#232326] rounded-2xl p-4">
  <h3 class="font-bold">Comments • {{ project.comments.count }}</h3>
  
  <!-- Form (only if logged in) -->
  <form method="post" action="{% url 'post_comment' project.slug %}" class="mt-3">
    {% csrf_token %}
    <textarea name="body" placeholder="Ask about setup, report a bug, share a vibe..." class="w-full bg-[#07070A] border border-[#232326] rounded-xl p-3 text-[13px] min-h-[80px]"></textarea>
    <div class="flex justify-between items-center mt-2">
      <span class="text-[11px] text-[#6A6A7A]">Markdown supported • Be respectful</span>
      <button class="bg-[#7C3AED] text-white px-4 py-2 rounded-xl text-[13px] font-bold">Comment</button>
    </div>
  </form>

  <!-- List -->
  <div class="mt-4 space-y-3">
    {% for c in comments %}
    <div class="flex gap-3 p-3 bg-[#07070A] border border-[#232326] rounded-xl">
      <div class="w-8 h-8 rounded-full bg-gradient-to-br from-violet-500 to-orange-400 grid place-items-center font-bold text-xs">{{ c.user.username|first|upper }}</div>
      <div class="flex-1">
        <div class="flex gap-2 items-center"><b class="text-[13px]">@{{ c.user.username }}</b><span class="text-[11px] text-[#6A6A7A]">{{ c.created_at|timesince }} ago</span>{% if c.is_edited %}<span class="text-[11px] text-[#6A6A7A]">• edited</span>{% endif %}</div>
        <div class="prose prose-invert prose-sm mt-1 max-w-none text-[13px] leading-relaxed">{{ c.body_html|safe }}</div>
        <button class="text-[11px] text-[#7C3AED] mt-1" onclick="replyTo({{ c.id }})">Reply</button>
        {% for r in c.replies.all %}
          <div class="mt-2 ml-4 pl-3 border-l border-[#232326]">{{ r.body_html|safe }}</div>
        {% endfor %}
      </div>
      <button class="text-[#6A6A7A]">⋯</button> <!-- Report/Hide -->
    </div>
    {% endfor %}
  </div>
</section>
```

---

## 4. HOW IT ALL CONNECTS — "stock_app_vibes" ACCESS FLOW

1.  Visitor lands on `/app/stock-app-vibes/` 
2.  Sees **Hero:** Title, ★, ⬇, `🤖 AI` + **Clone Box** (Copy `git clone`)
3.  **Must see:** `README` tab (default) → what it is, how to run → builds trust
4.  Clicks **Files** → sees tree `stock_app_vibes/frontend/src/App.jsx` → clicks to preview code (Prism, no execution)
5.  Scrolls to **Comments** → reads "Works on Django 5? — Yes, pip install -r requirements..." → decides to clone
6.  Clicks **Download ZIP** or copies `git clone` → Django logs clone (+1), redirects to signed S3 URL

**Without README + Tree + Comments, `stock_app_vibes` is just a blind ZIP.** With them, it's explorable like GitHub — people can audit before they run.

---

## 5. SECURITY FOR THIS FEATURE

- **README & Comments Markdown:** Always `nh3.clean()` / `bleach.clean()` before `|safe` (see Audit doc)
- **File Preview:** Read file from ZIP, `html.escape()` then `Prism.highlight()` — never `eval`
- **Tree Path:** Validate `file_path` has no `..` before reading from ZIP/S3
- **Comment Spam:** `django-ratelimit` + `django-honeypot` + max 2000 chars + no HTML allowed (markdown only)

---

**This spec is now ready to be built into the Figma Frame 02 (Detail) — I can update `BlaqVibes_Figma.html` to show the Tree + Comments tabs live, or generate the Django starter with `Comment` model + tree builder. Which do you want next?**
