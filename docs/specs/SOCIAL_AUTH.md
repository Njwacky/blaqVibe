# Social login — what you must do

Code is wired (Google, GitHub, Facebook). **Buttons stay hidden until you put real client IDs in `.env`.** Password login still works.

Redirect URIs must match the host people actually use (`SITE_URL`), including `https`.

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

## 4. Facebook (optional)

1. [Meta for Developers](https://developers.facebook.com/apps/) → Create app → Consumer / Authenticate.
2. Add Facebook Login → Settings → Valid OAuth Redirect URIs = Facebook callback.
3. App Dashboard → Settings → Basic → App ID + App Secret.
4. Switch the app **Live** only after you add a privacy policy URL.

```
FACEBOOK_CLIENT_ID=
FACEBOOK_CLIENT_SECRET=
```

Facebook will refuse `localhost` unless you add it under the app’s settings.

## 5. Restart and migrate

```bash
pip install -r requirements.txt
python manage.py migrate
# set SITE_URL + provider keys in .env
DEBUG=1 python manage.py runserver 0.0.0.0:8000
```

Open `/accounts/login/` — configured providers show as buttons.

## Behaviour

- Same email as an existing account **connects** (no second user).
- New Google/GitHub users get a Profile; GitHub `login` is copied to `profile.github`.
- Provider-verified email sets `profile.email_verified`.
- Secrets stay in env. Templates only get provider slugs that have a client id.

## Common failures

| Symptom | Fix |
|---------|-----|
| No buttons | Empty `GOOGLE_CLIENT_ID` etc. Restart after editing `.env`. |
| `redirect_uri_mismatch` | Callback URL in the console ≠ exact host/path/scheme. |
| `SocialApp matching query does not exist` | Both `CLIENT_ID` and `SECRET` must be set so `SOCIALACCOUNT_PROVIDERS['google']['APP']` is filled. |
| Wrong callback host | Update Site.domain and `SITE_URL`. |
| HTTPS required | Google/Facebook production apps need https. |
