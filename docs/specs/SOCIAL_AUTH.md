# Social login (OAuth) — what you must do

Google, GitHub and Facebook sign-in are wired end to end. **A provider's button
stays hidden until BOTH its client id and its secret are in `.env`.** Password
login always keeps working.

Redirect URIs must match the host people actually use (`SITE_URL`), including
the `https`.

| Provider | Callback path |
|----------|----------------|
| Google | `{SITE_URL}/accounts/social/google/login/callback/` |
| GitHub | `{SITE_URL}/accounts/social/github/login/callback/` |
| Facebook | `{SITE_URL}/accounts/social/facebook/login/callback/` |

Local example: `http://127.0.0.1:8000/accounts/social/google/login/callback/`

## 1. Django Site row

allauth uses `django.contrib.sites`. After migrate:

```bash
python manage.py shell -c "from django.contrib.sites.models import Site; s=Site.objects.get(id=1); s.domain='blaqvibes.co.za'; s.name='BlaqVibes'; s.save()"
```

For local: `domain='127.0.0.1:8000'`.

## 2. Google

1. [Google Cloud Console](https://console.cloud.google.com/apis/credentials) → create a project.
2. APIs & Services → OAuth consent screen → External → app name BlaqVibes → add your email → scopes `email`, `profile`, `openid`.
3. Credentials → Create credentials → OAuth client ID → **Web application**.
4. Authorized JavaScript origins: `https://blaqvibes.co.za` and `http://127.0.0.1:8000`.
5. Authorized redirect URIs: the Google callback above (prod + local).
6. Copy Client ID + Client secret into `.env`:

```
GOOGLE_CLIENT_ID=....apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-...
```

## 3. GitHub

1. GitHub → Settings → Developer settings → [OAuth Apps](https://github.com/settings/developers) → New OAuth App.
2. Homepage URL: `https://blaqvibes.co.za`
3. Authorization callback URL: the GitHub callback above (one URL per app — make a second OAuth app for localhost if you need both).
4. Generate a client secret.

```
GITHUB_CLIENT_ID=
GITHUB_CLIENT_SECRET=
```

We request `read:user` and `user:email`. The second one matters: a GitHub user
can keep every address private, in which case `/user` returns `email: null` and
we read the verified primary from `/user/emails` instead.

## 4. Facebook

1. [Meta for Developers](https://developers.facebook.com/apps/) → Create app → **Authenticate and request data from users with Facebook Login**.
2. Add Facebook Login → Settings → Valid OAuth Redirect URIs = the Facebook callback above.
3. App Dashboard → App settings → Basic → App ID + App Secret.
4. Permissions: `email` and `public_profile` only. Both are granted without App
   Review, so you do **not** need review for sign-in.
5. Switch the app **Live** (App settings → Basic) once you have a privacy policy
   URL. While it is in Development mode only app admins/testers can sign in.

```
FACEBOOK_CLIENT_ID=
FACEBOOK_CLIENT_SECRET=
```

Facebook refuses plain `localhost` redirects on a Live app. Test locally while
the app is in Development mode, or use a tunnelled https URL.

### Graph API version

The Graph API version is pinned in `settings.SOCIAL_PROVIDER_CREDENTIALS`
(currently `v25.0`, supported until 2028-07-29). Meta retires each version about
two years after release — `v19.0`, which django-allauth still defaults to, was
retired on 2026-05-21 and returns errors today.

**When you bump it:** change `VERSION` in that one dict and update the assertion
in `users/test_social_auth.py::test_authorize_url_uses_the_pinned_version`.
Check the [Graph API changelog](https://developers.facebook.com/docs/graph-api/changelog/)
for the current version and its expiry.

## 5. Restart and migrate

```bash
pip install -r requirements.txt
python manage.py migrate
# set SITE_URL + provider keys in .env
DEBUG=1 python manage.py runserver 0.0.0.0:8000
```

Open `/accounts/login/` — configured providers show as buttons.

## Behaviour

- **Same email connects, it does not duplicate.** A provider address that is
  verified and already belongs to a BlaqVibes account signs into that account
  and links the provider to it.
- **New users get a Profile**, and the GitHub `login` handle is copied to
  `profile.github` (an existing hand-typed handle is never overwritten).
- **A provider-verified email sets `profile.email_verified`** and therefore pays
  the one-time welcome star grant. This is ledger-idempotent: signing in again,
  or later clicking the email link, pays nothing more.
- **Reserved and profane handles are blocked on this path too.** A GitHub user
  called `admin` does not become `@admin` here; allauth falls through to a
  suffixed candidate.
- **No access tokens are stored** (`SOCIALACCOUNT_STORE_TOKENS = False`). We
  authenticate the person and forget the credential.
- **The handshake needs a POST.** `SOCIALACCOUNT_LOGIN_ON_GET` is off, so a
  third-party page cannot start a sign-in by embedding our URL.
- **Users manage links** at Settings → Connected accounts (`/accounts/social/`).
  The last sign-in method cannot be disconnected from an account with no
  password — that would lock the owner out.
- Secrets stay in env. Templates only ever receive provider slugs and labels.

## Common failures

| Symptom | Fix |
|---------|-----|
| No buttons | A client id or its secret is blank. Both halves are required. Restart after editing `.env`. |
| Button 404s | That provider has no credentials, so its route is not served. Same fix as above. |
| `redirect_uri_mismatch` | The callback URL in the provider console differs from the generated one — check scheme, host, and the trailing slash. |
| Callback lands on `http://` behind a proxy | Set `SITE_URL` to the `https://` origin; `ACCOUNT_DEFAULT_HTTP_PROTOCOL` follows it. |
| `SocialApp matching query does not exist` | Credentials went missing after boot. Both `CLIENT_ID` and `SECRET` must be set. |
| Facebook: "URL blocked" | The callback is not in Valid OAuth Redirect URIs. |
| Facebook: only you can sign in | The app is still in Development mode. Switch it Live. |
| Facebook: unsupported version | `VERSION` in `SOCIAL_PROVIDER_CREDENTIALS` has been retired — bump it. |
| GitHub user has no email | Expected when their addresses are private and `user:email` was not granted; they get the "finish signing up" form. |
