import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE','blaqvibes.settings')
django.setup()
from django.contrib.auth.models import User
from gallery.models import Category, AppProject

u,_ = User.objects.get_or_create(username='blaq')
u.set_password('blaq12345'); u.save()
from users.models import Profile
Profile.objects.get_or_create(user=u)

cats = {}
for slug,name,typ in [('landing-pages','Landing Pages','snippet'),('dashboard','Dashboards','snippet'),('track-stock','Track Stock','snippet')]:
    c,_=Category.objects.get_or_create(slug=slug, defaults={'name':name,'type':typ,'order':1})
    cats[slug]=c

templates = [
{
'slug':'saas-launch-hero-pro',
'title':'SaaS Launch Hero Pro — Real Template',
'cat':'landing-pages',
'short':'Real Tailwind hero with nav, badge, gradient H1, 2 CTAs, social proof bar — copy-paste.',
'tech':'Tailwind CSS',
'readme':"""# SaaS Launch Hero Pro
Real landing hero — copy HTML + CSS, add Tailwind CDN.

## How to use
1. Copy HTML below
2. Add to your page
3. Ensure Tailwind CDN: <script src="https://cdn.tailwindcss.com"></script>

## Features
- Nav, badge, gradient heading, dual CTA, logos bar
- No JS, pure Tailwind
""",
'html':"""<nav class="max-w-6xl mx-auto px-6 py-4 flex items-center justify-between">
  <div class="font-bold text-lg">LaunchCo</div>
  <div class="hidden md:flex items-center gap-6 text-sm text-gray-600"><a href="#">Features</a><a href="#">Pricing</a><a href="#">Docs</a></div>
  <div class="flex items-center gap-3"><a href="#" class="text-sm">Sign in</a><a href="#" class="bg-black text-white px-4 py-2 rounded-lg text-sm font-semibold">Get Started</a></div>
</nav>
<section class="max-w-6xl mx-auto px-6 py-16 text-center">
  <span class="inline-flex items-center gap-2 bg-violet-50 text-violet-700 px-3 py-1 rounded-full text-xs font-semibold">✨ New: AI analytics</span>
  <h1 class="mt-4 text-4xl md:text-5xl font-extrabold tracking-tight leading-tight">Build faster with <em class="text-violet-600 not-italic">LaunchCo</em></h1>
  <p class="mt-4 text-gray-600 max-w-2xl mx-auto">The all-in-one platform for founders to ship landing pages in minutes. No code, just copy-paste vibes.</p>
  <div class="mt-8 flex items-center justify-center gap-3"><a href="#" class="bg-black text-white px-6 py-3 rounded-xl font-semibold">Start free trial</a><a href="#" class="border border-gray-200 px-6 py-3 rounded-xl font-semibold">View demo →</a></div>
  <div class="mt-10 flex items-center justify-center gap-6 opacity-60 text-xs tracking-widest">TRUSTED BY <span>Linear</span> <span>Vercel</span> <span>Stripe</span></div>
</section>""",
'css':"""/* Tailwind via CDN — no extra CSS. Add: <script src="https://cdn.tailwindcss.com"></script> */
section{font-family:Inter, sans-serif}"""
},
{
'slug':'waitlist-minimal-real',
'title':'Waitlist Minimal — Real Template',
'cat':'landing-pages',
'short':'Centered waitlist with glow, email input + button — high converting.',
'tech':'Tailwind CSS',
'readme':"""# Waitlist Minimal
High converting waitlist.

## Use
Copy HTML/CSS, add Tailwind CDN.
""",
'html':"""<section class="min-h-[400px] grid place-items-center px-6 py-16">
  <div class="max-w-lg w-full text-center">
    <div class="w-12 h-12 rounded-xl bg-gradient-to-br from-violet-600 to-pink-500 grid place-items-center text-white mx-auto">✦</div>
    <h1 class="mt-4 text-3xl font-extrabold">Something amazing is coming</h1>
    <p class="mt-2 text-gray-600">Join 10,000+ waiting for launch. We’ll tell you when it’s uploaded.</p>
    <form class="mt-6 flex gap-2 justify-center" onsubmit="event.preventDefault(); alert('Joined!')">
      <input placeholder="Enter your email" class="flex-1 max-w-xs border border-gray-200 rounded-xl px-4 py-3 text-sm outline-none focus:ring-2 focus:ring-violet-500">
      <button class="bg-black text-white px-6 py-3 rounded-xl font-semibold text-sm">Join waitlist</button>
    </form>
  </div>
</section>""",
'css':"""/* Tailwind CDN only */"""
},
{
'slug':'analytics-dashboard-real',
'title':'Analytics Dashboard — Real Template',
'cat':'dashboard',
'short':'Real dashboard: sidebar + topbar + 3 KPI cards + chart placeholder + table — copy.',
'tech':'Tailwind CSS',
'readme':"""# Analytics Dashboard
Real snippet: sidebar + stats + chart + table.

## How to use
Copy HTML, add Tailwind CDN. Chart is placeholder — plug Chart.js.
""",
'html':"""<div class="min-h-[500px] grid grid-cols-[220px_1fr] bg-gray-50">
  <aside class="bg-slate-900 text-white p-4">
    <div class="font-bold">● Dashboard</div>
    <nav class="mt-6 grid gap-2 text-sm"><a class="bg-slate-800 rounded-lg px-3 py-2">Overview</a><a class="text-slate-400 px-3 py-2">Analytics</a><a class="text-slate-400 px-3 py-2">Orders</a><a class="text-slate-400 px-3 py-2">Settings</a></nav>
  </aside>
  <main class="p-6">
    <div class="grid grid-cols-3 gap-4">
      <div class="bg-white border border-gray-200 rounded-xl p-4"><div class="text-xs text-gray-500">Revenue</div><div class="text-xl font-bold">$42,430</div><div class="text-xs text-green-600">+12%</div></div>
      <div class="bg-white border border-gray-200 rounded-xl p-4"><div class="text-xs text-gray-500">Users</div><div class="text-xl font-bold">12,340</div><div class="text-xs text-green-600">+8%</div></div>
      <div class="bg-white border border-gray-200 rounded-xl p-4"><div class="text-xs text-gray-500">Orders</div><div class="text-xl font-bold">1,204</div><div class="text-xs text-red-500">-2%</div></div>
    </div>
    <div class="mt-4 bg-white border border-gray-200 rounded-xl p-4"><div class="text-sm font-semibold">Revenue Chart</div><div class="mt-3 h-32 bg-gray-50 border border-dashed border-gray-200 rounded-lg grid place-items-center text-gray-400 text-sm">Chart.js here</div></div>
    <div class="mt-4 bg-white border border-gray-200 rounded-xl p-4"><div class="text-sm font-semibold">Recent Orders</div><table class="w-full text-sm mt-3"><tr class="text-xs text-gray-500"><th class="text-left py-2">Customer</th><th>Amount</th><th>Status</th></tr><tr class="border-t"><td class="py-2">Thando</td><td>$120</td><td class="text-green-600">Paid</td></tr><tr class="border-t"><td class="py-2">Zandi</td><td>$89</td><td class="text-yellow-600">Pending</td></tr></table></div>
  </main>
</div>""",
'css':"""/* Tailwind CDN only */"""
},
{
'slug':'dark-saas-dashboard-real',
'title':'Dark SaaS Dashboard — Real Template',
'cat':'dashboard',
'short':'Dark mode admin with revenue + expenses + chart — Tailwind.',
'tech':'Tailwind CSS',
'readme':"""# Dark SaaS Dashboard
Dark admin — copy HTML.
""",
'html':"""<div class="min-h-[400px] bg-slate-950 text-white p-6">
  <div class="grid grid-cols-2 gap-4">
    <div class="bg-slate-900 border border-slate-800 rounded-xl p-4"><div class="text-xs text-slate-400">Revenue</div><div class="text-2xl font-bold">$84,000</div></div>
    <div class="bg-slate-900 border border-slate-800 rounded-xl p-4"><div class="text-xs text-slate-400">Expenses</div><div class="text-2xl font-bold">$21,400</div></div>
  </div>
  <div class="mt-4 bg-slate-900 border border-slate-800 rounded-xl p-4"><div class="text-sm font-semibold">Monthly Revenue</div><div class="h-32 bg-slate-950 border border-dashed border-slate-800 rounded-lg mt-3 grid place-items-center text-slate-500 text-sm">Chart area</div></div>
</div>""",
'css':"""/* Tailwind CDN only */"""
},
{
'slug':'stock-portfolio-table-real',
'title':'Stock Portfolio Table — Real Template',
'cat':'track-stock',
'short':'Real stock table with symbol, price, change, sparkline — Tailwind.',
'tech':'Tailwind CSS',
'readme':"""# Stock Portfolio Table
Real table — plug TwelveData or mock.

## Features
- Symbol, Price, Change, Sparkline, Buy button
""",
'html':"""<div class="max-w-3xl mx-auto p-6">
  <div class="flex items-center justify-between"><h2 class="font-bold text-lg">My Portfolio</h2><span class="bg-green-50 text-green-700 px-3 py-1 rounded-full text-xs font-bold">● LIVE</span></div>
  <div class="mt-4 bg-white border border-gray-200 rounded-xl overflow-hidden">
    <table class="w-full text-sm">
      <thead class="text-xs text-gray-500"><tr><th class="text-left px-4 py-3">Symbol</th><th>Price</th><th>Change</th><th>Chart</th><th></th></tr></thead>
      <tbody>
        <tr class="border-t"><td class="px-4 py-3 font-bold">AAPL</td><td>$182.52</td><td class="text-green-600 font-semibold">+2.34%</td><td class="font-mono text-xs">▁▂▃▅▇</td><td><button class="bg-black text-white px-3 py-1 rounded-lg text-xs">Trade</button></td></tr>
        <tr class="border-t"><td class="px-4 py-3 font-bold">TSLA</td><td>$248.50</td><td class="text-red-600 font-semibold">-1.12%</td><td class="font-mono text-xs">▇▅▃▂▁</td><td><button class="bg-black text-white px-3 py-1 rounded-lg text-xs">Trade</button></td></tr>
        <tr class="border-t"><td class="px-4 py-3 font-bold">NVDA</td><td>$903.12</td><td class="text-green-600 font-semibold">+5.80%</td><td class="font-mono text-xs">▂▃▅▆▇</td><td><button class="bg-black text-white px-3 py-1 rounded-lg text-xs">Trade</button></td></tr>
      </tbody>
    </table>
  </div>
</div>""",
'css':"""/* Tailwind CDN only — table is ready */"""
},
{
'slug':'crypto-chart-card-real',
'title':'Crypto Chart Card — Real Template',
'cat':'track-stock',
'short':'Real card: price, change, chart placeholder, Buy/Sell — dark/light.',
'tech':'Tailwind CSS',
'readme':"""# Crypto Chart Card
Single asset card.
""",
'html':"""<div class="max-w-sm mx-auto border border-gray-200 rounded-2xl p-4">
  <div class="flex items-center justify-between"><div class="font-bold">BTC <span class="text-gray-500 font-normal">$67,420</span></div><span class="text-green-600 font-bold text-sm">▲ 4.2%</span></div>
  <div class="mt-3 h-24 bg-gradient-to-t from-amber-50 to-transparent border border-dashed border-amber-200 rounded-xl grid place-items-center text-amber-800 text-sm">Candlestick / TradingView here</div>
  <div class="mt-3 flex gap-2"><button class="flex-1 bg-green-600 text-white py-2 rounded-xl font-semibold text-sm">Buy</button><button class="flex-1 border border-gray-200 py-2 rounded-xl font-semibold text-sm">Sell</button></div>
</div>""",
'css':"""/* Tailwind CDN only */"""
},
]

for t in templates:
    if AppProject.objects.filter(slug=t['slug']).exists():
        print("exists", t['slug'])
        continue
    p=AppProject.objects.create(
        owner=u, title=t['title'], slug=t['slug'], category=cats[t['cat']],
        short_description=t['short'], readme=t['readme'], tech_stack=t['tech'],
        html_code=t['html'], css_code=t['css'], status='published'
    )
    # tree for snippet? just html
    p.file_count=1
    p.save()
    print("created", p.slug)

print("DONE", AppProject.objects.filter(status='published').count())
