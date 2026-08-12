# How to Build a Copy-&-Paste HTML/CSS Template Gallery with Django

Yes, it's **100% possible and very common** with Django. Think of sites like **HTMLrev, Flowbite, UI Verse, Tailwind UI** — you can build the exact same thing.

Django is perfect for this because you just need to: **Store templates -> Categorize them -> Show previews -> Let users copy code.**

---

### 1. How The App Works (Architecture)

```
[Admin uploads HTML/CSS] -> [Django DB: Template model] -> [Gallery Page]
                                                            |
                                                            -> Preview in iframe / live render
                                                            -> "Copy HTML" button (JavaScript)
                                                            -> "Copy CSS" button
                                                            -> Separate .html file download
[User] -> Browse by Category (Landing / Dashboard / Stock Tracker) -> Search/Filter -> Copy & Paste to their project
```

You have **TWO options** for storage:
1.  **Store code in Database (Recommended for copy-paste gallery):** Each template is a row with `html_code` and `css_code` TextFields.
2.  **Store as static files:** Each template is a folder with `index.html` + `style.css`. Harder to copy.

Go with **Option 1**.

### 2. Project Structure

```bash
django-admin startproject core .
python manage.py startapp gallery

core/
gallery/
  models.py       # Template, Category
  views.py        # List, Detail, Preview
  urls.py
  admin.py
  templates/gallery/
    base.html
    template_list.html  # The gallery grid
    template_detail.html # Preview + Copy buttons
    preview.html        # Isolated preview (iframe)
  static/gallery/
    css/style.css
db.sqlite3
```

### 3. Step-by-Step Implementation

#### Step 1: Models (`gallery/models.py`)
This is the heart of it.

```python
from django.db import models

class Category(models.Model):
    name = models.CharField(max_length=100) # e.g. Landing Page, Dashboard, Track Stock
    slug = models.SlugField(unique=True)

    def __str__(self):
        return self.name

class Template(models.Model):
    title = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='templates')
    description = models.TextField(blank=True)
    
    # The actual code to be copied
    html_code = models.TextField(help_text="Full HTML code")
    css_code = models.TextField(help_text="Full CSS code", blank=True)
    
    thumbnail = models.ImageField(upload_to='thumbnails/', blank=True, null=True)
    preview_image = models.URLField(blank=True, help_text="Optional external preview img")
    
    is_free = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title
```

Run `python manage.py makemigrations && python manage.py migrate` and register in `admin.py` so you can paste templates via Django Admin.

#### Step 2: Views (`gallery/views.py`)

```python
from django.shortcuts import render, get_object_or_404
from .models import Template, Category

def template_list(request):
    category_slug = request.GET.get('category')
    q = request.GET.get('q')
    
    templates = Template.objects.all().select_related('category')
    
    if category_slug:
        templates = templates.filter(category__slug=category_slug)
    if q:
        templates = templates.filter(title__icontains=q)
    
    categories = Category.objects.all()
    return render(request, 'gallery/template_list.html', {
        'templates': templates,
        'categories': categories
    })

def template_detail(request, slug):
    template = get_object_or_404(Template, slug=slug)
    return render(request, 'gallery/template_detail.html', {'template': template})

def template_preview(request, slug):
    # Rendered inside an <iframe> - isolated, no site CSS leaking
    template = get_object_or_404(Template, slug=slug)
    return render(request, 'gallery/preview.html', {'template': template})
```

#### Step 3: URLs (`gallery/urls.py`)

```python
from django.urls import path
from . import views

urlpatterns = [
    path('', views.template_list, name='template-list'),
    path('<slug:slug>/', views.template_detail, name='template-detail'),
    path('<slug:slug>/preview/', views.template_preview, name='template-preview'),
]
```

#### Step 4: The Magic - Copy & Paste UI (`template_detail.html`)

Use **Prism.js** for syntax highlighting and a simple JS copy button.

```html
{% extends 'gallery/base.html' %}

<div class="preview-wrapper">
  <!-- Live preview via iframe -->
  <iframe src="{% url 'template-preview' template.slug %}" style="width:100%; height:600px; border:1px solid #ddd; border-radius:12px;"></iframe>
</div>

<div class="code-tabs">
  <div class="tabs">
    <button onclick="showTab('html')">HTML</button>
    <button onclick="showTab('css')">CSS</button>
  </div>

  <div id="html-code" class="code-block">
    <button class="copy-btn" onclick="copyCode('html-code-pre')">Copy HTML</button>
    <pre><code id="html-code-pre" class="language-html">{{ template.html_code }}</code></pre>
  </div>

  <div id="css-code" class="code-block hidden">
    <button class="copy-btn" onclick="copyCode('css-code-pre')">Copy CSS</button>
    <pre><code id="css-code-pre" class="language-css">{{ template.css_code }}</code></pre>
  </div>
</div>

<script>
function copyCode(elementId) {
  const code = document.getElementById(elementId).innerText;
  navigator.clipboard.writeText(code).then(() => {
    alert('Copied to clipboard!');
  });
}
function showTab(type) {
  document.getElementById('html-code').classList.toggle('hidden', type !== 'html');
  document.getElementById('css-code').classList.toggle('hidden', type !== 'css');
}
</script>
```

For `preview.html` (very important - renders ONLY the template code):

```html
<!DOCTYPE html>
<html>
<head>
  <style>{{ template.css_code|safe }}</style>
</head>
<body>
  {{ template.html_code|safe }}
</body>
</html>
```

### 4. Key Features to Add

| Feature | How to do it in Django |
| :--- | :--- |
| **Categories** | `Category` model + `?category=landing-pages` filter |
| **Search** | `title__icontains` or Postgres full-text search |
| **Copy button** | `navigator.clipboard.writeText()` (JS, no Django needed) |
| **Download as .zip** | Create a view that zips `html_code` + `css_code` on the fly |
| **Live Preview** | `iframe` pointing to `preview.html` |
| **Syntax Highlighting** | Add `Prism.js` or `highlight.js` CDN |
| **Code Editor look** | Use `CodeMirror` or `Monaco` if you want editable preview |

### 5. For Your 3 Example Categories

1.  **Landing Pages:** Hero sections, CTA, pricing tables. Store as single HTML file with embedded CSS.
2.  **Dashboard:** Sidebar + navbar + cards + charts. Use Tailwind/Bootstrap classes inside `html_code`.
3.  **Track Stock Pages:** Tables with stock symbols, price, change %, charts. Add `stock_data` dummy JSON for preview.

### 6. Deployment & Next Steps

- Use Django Admin to add templates — no need to build a custom upload form at first.
- For better CSS isolation, use **Tailwind CSS CDN** inside preview iframe.
- Host on Render / PythonAnywhere / DigitalOcean.

---

### Want a Starter Project Generated?

I can generate a complete, runnable Django starter project for you right now with:
- 3 categories pre-created (Landing, Dashboard, Track Stock)
- 6 example templates with real HTML/CSS
- Working copy buttons and live iframe preview
- Admin panel ready

Just say **"Generate the starter project"** and I'll build it in your workspace.

Also check the interactive prototype I built for you: `template-gallery-prototype.html`
