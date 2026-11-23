# Microsoft sign-in setup for the AVAIBE LinkedIn Intelligence app

**Who this is for:** AVAIBE's Microsoft 365 / Entra administrator.
**Time needed:** about 10 minutes.
**What it does:** lets AVAIBE staff sign in to the internal LinkedIn analytics app with their normal work
account. Only AVAIBE accounts can sign in, and the app grants access only to people an app admin has invited.

---

## 1. Register the app

1. Open the **Microsoft Entra admin center** (entra.microsoft.com) → **Identity** → **Applications** →
   **App registrations** → **New registration**.
2. Fill in:
   - **Name:** `avaibe LinkedIn Intelligence`
   - **Supported account types:** *Accounts in this organizational directory only (AVAIBE only – Single tenant)*
   - **Redirect URI:** platform **Web**, value `http://localhost:5173/api/auth/callback`
3. Click **Register**.

> When the app is hosted later, add the hosted address as a second redirect URI
> (e.g. `https://<hosted-address>/api/auth/callback`) under **Authentication**.

## 2. Copy two IDs

On the app's **Overview** page, copy:
- **Application (client) ID**
- **Directory (tenant) ID**

These are not secret.

## 3. Create a client secret

1. **Certificates & secrets** → **Client secrets** → **New client secret**.
2. Description `app login`, expiry **12 months** (put a renewal reminder in the calendar).
3. Copy the **Value** immediately (it is shown only once).

**This value is a password.** Don't send it by plain email or chat. Either type it directly into the app's
configuration file yourself, or share it through a password manager.

## 4. Permissions

**API permissions** should list **Microsoft Graph → User.Read** (added by default). Also make sure these delegated
permissions are present: `openid`, `profile`, `email`. Then click **Grant admin consent for AVAIBE**, so staff
aren't asked to consent individually.

## 5. (Optional) Restrict at the Microsoft level as well

The app already only lets in people an app admin has invited. For an extra layer:
**Enterprise applications** → `avaibe LinkedIn Intelligence` → **Properties** → **Assignment required? = Yes**,
then **Users and groups** → add the staff or group who should use it.

---

## Hand back to the app owner

| Item | Secret? |
|---|---|
| Directory (tenant) ID | No |
| Application (client) ID | No |
| Client secret value | **Yes** (enter it directly into `backend/.env`, or share via a password manager) |

They go into `backend/.env` as:

```
MS_TENANT_ID=<Directory (tenant) ID>
MS_CLIENT_ID=<Application (client) ID>
MS_CLIENT_SECRET=<client secret value>
```
