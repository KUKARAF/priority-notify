# Setting Up Authentik for priority-notify

This guide walks through creating an OAuth2/OIDC provider and application in Authentik so that priority-notify can authenticate users.

## Prerequisites

- A running Authentik instance (this guide assumes `https://auth.osmosis.page`)
- Admin access to the Authentik admin interface

## 1. Create an OAuth2/OpenID Provider

1. Go to **Applications > Providers** and click **Create**
2. Select **OAuth2/OpenID Provider**
3. Fill in the settings:

| Field | Value |
|-------|-------|
| **Name** | `priority-notify` |
| **Authentication flow** | Use your default authentication flow |
| **Authorization flow** | Use your default authorization flow (e.g. `default-provider-authorization-implicit-consent` for no consent screen, or `default-provider-authorization-explicit-consent` if you want one) |
| **Client type** | **Confidential** |
| **Client ID** | Auto-generated — copy this value for `.env` |
| **Client Secret** | Auto-generated — copy this value for `.env` |
| **Redirect URIs/Origins** | `http://localhost:8000/auth/callback` (dev) and your production URL, e.g. `https://notify.example.com/auth/callback` |

4. Under **Advanced protocol settings**:
   - **Scopes**: Ensure `openid`, `email`, and `profile` are selected
   - **Subject mode**: Based on the User's hashed ID (default is fine)
   - **Token validity**: Defaults are fine

5. Click **Finish**

## 2. Create an Application

1. Go to **Applications > Applications** and click **Create**
2. Fill in the settings:

| Field | Value |
|-------|-------|
| **Name** | `priority-notify` |
| **Slug** | `priority-notify` |
| **Provider** | Select the `priority-notify` provider you just created |
| **Launch URL** | `https://notify.example.com/` (or your server URL) |

3. Click **Create**

> **Important**: The application slug must match the `AUTHENTIK_CLIENT_ID` value, because the server constructs the OIDC discovery URL as:
> `{AUTHENTIK_ISSUER_URL}/application/o/{AUTHENTIK_CLIENT_ID}/.well-known/openid-configuration`

Wait — that's not right. Authentik uses the **application slug** in the discovery URL path, not the client ID. If your slug and client ID differ, you need to update the discovery URL pattern. By default the server uses the client ID. If your slug differs from your client ID, either:
- Make the slug match the client ID, **or**
- Set the slug as the client ID in your `.env` (Authentik doesn't care what you use as long as the actual client_id is sent in the OAuth2 requests — but the discovery URL uses the slug)

The simplest approach: **use the same value for both the application slug and the Client ID**. You can manually set the Client ID in the provider settings to match your slug.

## 3. Configure priority-notify

Copy the example env file and fill in the values from Authentik:

```bash
cp .env.example .env
```

Edit `.env`:

```bash
AUTHENTIK_ISSUER_URL=https://auth.osmosis.page
AUTHENTIK_CLIENT_ID=priority-notify
AUTHENTIK_CLIENT_SECRET=<the client secret from the provider>
AUTHENTIK_REDIRECT_URI=http://localhost:8000/auth/callback
SECRET_KEY=<generate with: python -c "import secrets; print(secrets.token_urlsafe(32))">
```

The `AUTHENTIK_CLIENT_ID` here should match the **application slug** in Authentik (used for OIDC discovery) and the **Client ID** in the provider settings (used in OAuth2 requests).

## 4. Verify the Setup

1. Start the server: `make dev`
2. Open `http://localhost:8000` — you should see a login page
3. Click "Sign in with Authentik" — you should be redirected to your Authentik login
4. After authenticating, you should be redirected back to the dashboard

## OIDC Claims Used

The server reads the following claims from the ID token:

| Claim | Used for |
|-------|----------|
| `sub` | Unique user identifier (stable across name/email changes) |
| `email` | Display and stored on User record |
| `name` | Display name (falls back to `preferred_username`) |

These are all included in the default `openid`, `email`, and `profile` scopes — no custom scopes or property mappings needed.

## Troubleshooting

**"301 Moved Permanently" on login** — The OIDC discovery URL is wrong. Check that `AUTHENTIK_CLIENT_ID` matches the application slug in Authentik. You can verify by opening `https://auth.osmosis.page/application/o/<slug>/.well-known/openid-configuration` in a browser.

**"Invalid audience" after callback** — The Client ID in your `.env` doesn't match what Authentik puts in the `aud` claim of the ID token. Ensure the Client ID in the provider settings matches `AUTHENTIK_CLIENT_ID` in `.env`.

**"Invalid issuer" after callback** — `AUTHENTIK_ISSUER_URL` doesn't match the `iss` claim in the ID token. Check the OIDC discovery endpoint to see what Authentik reports as the issuer.
